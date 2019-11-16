import os
import stat

from anki.consts import *
from anki.decks import defaultConf as defaultDeckConf
from anki.decks import defaultDeck, defaultDynamicDeck
from anki.lang import _, ngettext
from anki.utils import ids2str, intTime


class FixingManager:

    def __init__(self, col):
        self.col = col
        self.db = col.db

    def fixIntegrity(self):
        "Fix possible problems and rebuild caches."
        problems = []
        curs = self.db.cursor()
        self.col.save()
        oldSize = os.stat(self.col.path)[stat.ST_SIZE]

        # whether sqlite find a problem in its database
        if self.db.scalar("pragma integrity_check") != "ok":
            return (_("Collection is corrupt. Please see the manual."), False)

        # note types with a missing model
        ids = self.db.list(
            """select id from notes where mid not in """ + ids2str(self.col.models.ids()),
        )
        if ids:
            problems.append(
                ngettext("Deleted %d note with missing note type.",
                         "Deleted %d notes with missing note type.", len(ids))
                         % len(ids))
            self.col.remNotes(ids)

        # for each model
        for model in self.col.models.all():
            for template in model['tmpls']:
                if template['did'] == "None":
                    template['did'] = None
                    problems.append(_("Fixed AnkiDroid deck override bug."))
                    self.col.models.save(model)
        for model in self.col.models.all(MODEL_STD):
            # model with missing req specification
            if 'req' not in model:
                self.col.models._updateRequired(model)
                problems.append(_("Fixed note type: %s") % model['name'])
            # cards with invalid ordinal
            ids = self.db.list(
                """select id from cards where ord not in %s and nid in ( select id
                from notes where mid = ?)""" %
                ids2str([template['ord'] for template in model['tmpls']]),
                model['id'])
            if ids:
                problems.append(
                    ngettext("Deleted %d card with missing template.",
                             "Deleted %d cards with missing template.",
                             len(ids)) % len(ids))
                self.col.remCards(ids)
        for model in self.col.models.all():
            # notes with invalid field count
            ids = self.db.execute(
                """select id from notes where mid = ? and 
                (LEN(flds)-LEN(REPLACE(flds, '\x1f', ''))) <> ?""", model['id'])
            if ids:
                problems.append(
                    ngettext("Deleted %d note with wrong field count.",
                             "Deleted %d notes with wrong field count.",
                             len(ids)) % len(ids))
                self.col.remNotes(ids)
        # delete any notes with missing cards
        ids = self.db.list(
            """select id from notes where id not in
            (select distinct nid from cards)""",
        )
        if ids:
            cnt = len(ids)
            problems.append(
                ngettext("Deleted %d note with no cards.",
                         "Deleted %d notes with no cards.", cnt) % cnt)
            self.col._remNotes(ids)
        # cards with missing notes
        ids = self.db.list(
            """select id from cards where nid not in (select id from notes)""",
        )
        if ids:
            cnt = len(ids)
            problems.append(
                ngettext("Deleted %d card with missing note.",
                         "Deleted %d cards with missing note.", cnt) % cnt)
            self.col.remCards(ids)
        # cards with odue set when it shouldn't be
        ids = self.db.list(
            f"""select id from cards where odue > 0 and (type={CARD_LRN} or
            queue={CARD_DUE}) and not odid""",
        )
        if ids:
            cnt = len(ids)
            problems.append(
                ngettext("Fixed %d card with invalid properties.",
                         "Fixed %d cards with invalid properties.", cnt) % cnt)
            self.db.execute("update cards set odue=0 where id in "+
                ids2str(ids))
        # cards with odid set when not in a dyn deck
        dids = [id for id in self.col.decks.allIds() if not self.col.decks.isDyn(id)]
        ids = self.db.list("""
        select id from cards where odid > 0 and did in %s""" % ids2str(dids))
        if ids:
            cnt = len(ids)
            problems.append(
                ngettext("Fixed %d card with invalid properties.",
                         "Fixed %d cards with invalid properties.", cnt) % cnt)
            self.db.execute("update cards set odid=0, odue=0 where id in "+
                ids2str(ids))
        # tags
        self.col.tags.registerNotes()
        # field cache
        for model in self.col.models.all():
            self.col.updateFieldCache(self.col.models.nids(model))
        # new cards can't have a due position > 32 bits, so wrap items over
        # 2 million back to 1 million
        curs.execute(
            f"""update cards set due=1000000+due%1000000,mod=?,usn=? where
            due>=1000000 and type = {CARD_NEW}""",
            [intTime(), self.col.usn()])
        if curs.rowcount:
            problems.append("Found %d new cards with a due number >= 1,000,000 - consider repositioning them in the Browse screen." % curs.rowcount)
        # new card position
        self.col.conf['nextPos'] = self.db.scalar(
            f"""select max(due)+1 from cards where type = {CARD_NEW}""",
        ) or 0
        # reviews should have a reasonable due #
        ids = self.db.list(
            """select id from cards where queue = 2 and due > 100000""",
        )
        if ids:
            problems.append("Reviews had incorrect due date.")
            self.db.execute(
                """update cards set due = ?, ivl = 1, mod = ?, usn = ? where id in %s"""
                % ids2str(ids),
                self.col.sched.today, intTime(), self.col.usn())
        # v2 sched had a bug that could create decimal intervals
        curs.execute(
            """update cards set ivl=round(ivl),due=round(due) where ivl!=round(ivl) or due!=round(due)""",
        )
        if curs.rowcount:
            problems.append("Fixed %d cards with v2 scheduler bug." % curs.rowcount)

        curs.execute(
            """update revlog set ivl=round(ivl),lastIvl=round(lastIvl) where ivl!=round(ivl) or lastIvl!=round(lastIvl)""",
        )
        if curs.rowcount:
            problems.append("Fixed %d review history entries with v2 scheduler bug." % curs.rowcount)
        # models
        if self.col.models.ensureNotEmpty():
            problems.append("Added missing note type.")
        # and finally, optimize
        self.col.optimize()
        newSize = os.stat(self.col.path)[stat.ST_SIZE]
        txt = _("Database rebuilt and optimized.")
        ok = not problems
        print("Adding in collection.py")
        problems.append(txt)
        # if any problems were found, force a full sync
        if not ok:
            self.col.modSchema(check=False)
        self.col.save()
        return ("\n".join(problems), ok)
