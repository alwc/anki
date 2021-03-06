# -*- coding: utf-8 -*-
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
import pprint
import time
from copy import copy
from random import random

from anki.consts import *
from anki.hooks import runHook
from anki.lang import _
from anki.utils import (fmtTimeSpan, formatDay, htmlToTextLine, intTime,
                        joinFields, strftimeIfArgument, timeFmt, timestampID)

# Cards
##########################################################################

class Card:

    """
    Cards are what you review.
    There can be multiple cards for each note, as determined by the Template.

    id -- the epoch milliseconds of when the card was created
    nid -- The card's note's id
    did -- The card's deck id
    ord -- ordinal : identifies which of the card templates it corresponds to
    valid values are from 0 to num templates - 1
    mod -- modificaton time as epoch seconds
    usn -- update sequence number : see README.synchronization
    type -- -- 0=new, 1=learning, 2=due, 3=filtered. (3 only in sched v2)
    queue --
         -- QUEUE_SCHED_BURIED: card buried by scheduler, -3
         -- QUEUE_USER_BURIED: card buried by user, -2
         -- QUEUE_SUSPENDED: Card suspended, -1
         -- QUEUE_NEW: new card,  0
         -- QUEUE_LRN: cards in learning, which should be seen again today, 1
         -- QUEUE_REV: cards already learned, 2
         -- QUEUE_DAY_LRN: cards in learning but won't be seen today again, 3
         -- QUEUE_PREVIEW: cards for the preview mode, sched 2 :4
    due -- Due is used differently for different card types:
        --   new: note id or random int.
                  Allow to select in which order new cards are seens.
        --   due: integer day in which the card is due for the next time,
                  relative to the collection's creation time
        --   learning: integer timestamp of when the next review is due
    ivl -- interval (used in SRS algorithm). Negative = seconds, positive = days
    factor -- easyness factor (used in SRS algorithm)
    reps -- number of reviews to do
    lapses -- the number of times the card went from a "was answered correctly"
           --   to "was answered incorrectly" state
    left
      -- of the form a*1000+b, with:
      -- b the number of reps left till graduation
      -- a the number of reps left today
    odue -- original due: In filtered decks, it's the original due date that the card had before moving to filtered.
             If the card lapsed in scheduler1, then it's the value before the lapse. (This is used when switching to scheduler 2. At this time, cards in learning becomes due again, with their previous due date)
             In any other case it's 0.
    odid -- original did: only used when the card is currently in filtered deck
    flags -- an integer. This integer mod 8 represents a "flag", which can be see in browser and while reviewing a note. Red 1, Orange 2, Green 3, Blue 4, no flag: 0. This integer divided by 8 represents currently nothing
    data -- currently unused

    Values not in the database:
    col -- its collection
    timerStarted -- The time at which the timer started
    _qa -- the dictionnary whose element q and a are questions and answers html
    _note -- the note object of the card
    wasNew --
    """

    def __init__(self, col, id=None):
        """
        This function returns a card object from the collection given in argument.

        If an id is given, then the card is the one with this id from the collection.
        Otherwise a new card, assumed to be from this collection, is created.

        Keyword arguments:
        col -- a collection
        id -- an identifier of a card. Int.
        """
        self.col = col
        self.timerStarted = None
        self._qa = None
        self._note = None
        if id:
            self.id = id
            self.load()
        else:
            # to flush, set nid, ord, and due
            self.id = timestampID(col.db, "cards")
            self.did = 1
            self.crt = intTime()
            self.type = CARD_NEW
            self.queue = QUEUE_NEW
            self.ivl = 0
            self.factor = 0
            self.reps = 0
            self.lapses = 0
            self.left = 0
            self.odue = 0
            self.odid = 0
            self.flags = 0
            self.data = ""

    def load(self):
        """
        Given a card, complete it with the information extracted from the database.

        It is assumed that the card's id and col are already known."""
        (self.id,
         self.nid,
         self.did,
         self.ord,
         self.mod,
         self.usn,
         self.type,
         self.queue,
         self.due,
         self.ivl,
         self.factor,
         self.reps,
         self.lapses,
         self.left,
         self.odue,
         self.odid,
         self.flags,
         self.data) = self.col.db.first(
             "select * from cards where id = ?", self.id)
        self._qa = None
        self._note = None

    def flush(self):
        """Insert the card into the database.

        If the cards is already in the database, it is replaced,
        otherwise it is created."""
        self.mod = intTime()
        self.usn = self.col.usn()
        # bug check
        if self.queue == QUEUE_REV and self.odue and not self.currentDeck().isDyn():
            runHook("odueInvalid")
        assert self.due < 4294967296
        self.col.db.execute(
            """
insert or replace into cards values
(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            self.id,
            self.nid,
            self.did,
            self.ord,
            self.mod,
            self.usn,
            self.type,
            self.queue,
            self.due,
            self.ivl,
            self.factor,
            self.reps,
            self.lapses,
            self.left,
            self.odue,
            self.odid,
            self.flags,
            self.data)
        self.col.log(self)

    def flushSched(self):
        """Update the card into the database.

        This card is supposed to already
        exists in the db."""
        self.mod = intTime()
        self.usn = self.col.usn()
        # bug checks
        if self.queue == QUEUE_REV and self.odue and not self.currentDeck().isDyn():
            runHook("odueInvalid")
        assert self.due < 4294967296
        self.col.db.execute(
            """update cards set
mod=?, usn=?, type=?, queue=?, due=?, ivl=?, factor=?, reps=?,
lapses=?, left=?, odue=?, odid=?, did=? where id = ?""",
            self.mod,
            self.usn,
            self.type,
            self.queue,
            self.due,
            self.ivl,
            self.factor,
            self.reps,
            self.lapses,
            self.left,
            self.odue,
            self.odid,
            self.did,
            self.id)
        self.col.log(self)

    def copy(self, nid, copy_creation, copy_review, copy_log):
        """Create a copy of current card, for note nid.

        copy_review -- if False, it's similar to a new card. Otherwise, keep every current properties
        copy_creation -- whether to keep original creation date.
        copy_log -- whether to copy the logs of the cards of this note
        """
        card = copy(self)
        card_date = card.id if copy_creation else None
        card.id = timestampID(self.col.db, "cards", t=card_date)
        if not copy_review:
            card.type = 0
            card.ivl = 0
            card.factor = 0
            card.reps = 0
            card.lapses = 0
            card.left = 0
            card.odue = 0
        card.nid = nid
        card.flush()
        if copy_log:
            for id, cid, usn, ease, ivl, lastIvl, factor, time, type in self.col.db.all("select * from revlog where id = ?", self.id):
                id = timestampID(mw.col.db, "revlog", t=id)
                cid = card.id
                self.col.db.execute("insert into revlog values (?, ?, ?, ?, ?, ?, ?, ?, ?)", id, cid, usn, ease, ivl, lastIvl, factor, time, type)
        return card

    def q(self, reload=False, browser=False):
        """The card question with its css.

        Keyword arguments:
        reload -- whether the card should be reloaded even if it is already known
        browser -- whether its called from the browser (in which case the format strings are
        bqfmt and not qfmt)
        """
        return self.css() + self._getQA(reload, browser)['q']

    def a(self):
        """Return the card answer with its css"""
        return self.css() + self._getQA()['a']

    def css(self):
        """Return the css of the card's model, as html code"""
        return "<style>%s</style>" % self.model()['css']

    def _getQA(self, reload=False, browser=False):
        """The QA dictionnary of the card. This dictionnary is added to the card.

        If the QA dictionnary already exists and reload is not set to true, it is directly returned.

        Keyword arguments:
        browser -- ???TODO
        """
        if not self._qa or reload:
            note = self.note(reload)
            model = self.model()
            template = self.template()
            if browser:
                args = (template.get('bqfmt'), template.get('bafmt'))
            else:
                args = tuple()
            self._qa = self.col._renderQA(model, self.ord, note.joinedFields(), self.id, note.id, self.originalDid(), note.stringTags(), self.flags, *args)
        return self._qa

    def note(self, reload=False):
        """The note object of the card.

        If the cards already knows its object, and reload is not true,
        this object is used.
        """
        if not self._note or reload:
            self._note = self.col.getNote(self.nid)
        return self._note

    def model(self):
        """The card's note's model (note type) object. This object is
        described in models.py."""
        return self.col.models.get(self.note().mid, orNone=False)

    def template(self):
        """The card's template object. See models.py for a comment of this
        object."""
        model = self.model()
        return self.model().getTemplate(self.ord)

    def startTimer(self):
        """Start the timer of the card"""
        self.timerStarted = time.time()

    def timeLimit(self):
        """Time limit for answering in milliseconds.

        According to the deck's information."""
        conf = self.originalConf()
        return conf['maxTaken']*1000

    def shouldShowTimer(self):
        """Whether timer should be shown.

        According to the deck's information."""
        conf = self.originalConf()
        return conf['timer']

    def timeTaken(self):
        "Time taken to answer card, in integer MS."
        total = int((time.time() - self.timerStarted)*1000)
        return min(total, self.timeLimit())

    def isEmpty(self):
        """TODO"""
        ords = self.model().availOrds(joinFields(self.note().fields))
        if self.ord not in ords:
            return True

    def __repr__(self):
        values = dict(self.__dict__)
        # remove non-useful elements
        del values['_note']
        del values['_qa']
        del values['col']
        del values['timerStarted']
        return pprint.pformat(values, width=300)

    def userFlag(self):
        return self.flags & 0b111

    def setUserFlag(self, flag):
        assert 0 <= flag <= 7
        self.flags = (self.flags & ~0b111) | flag

    def isFiltered(self):
        return self.odid

    def currentDeck(self):
        return self.col.decks.get(self.did)

    def originalDid(self):
        """Independantly of whether the card is filtered or not."""
        return self.odid or self.did

    def originalDeck(self):
        """Independantly of whether the card is filtered or not."""
        return self.col.decks.get(self.originalDid())

    def originalConf(self):
        """Independantly of whether the card is filtered or not."""
        return self.originalDeck().getConf()

    def currentConf(self):
        return self.currentDeck().getConf()

    # Deck columns to show
    ######################################################################

    def questionBrowserColumn(self):
        return htmlToTextLine(self.q(browser=True))

    def answerBrowserColumn(self):
        if self.template().get('bafmt'):
            # they have provided a template, use it verbatim
            self.q(browser=True)
            return htmlToTextLine(self.a())
        # need to strip question from answer
        questionHtml = self.questionBrowserColumn()
        answerLine = htmlToTextLine(self.a())
        if answerLine.startswith(questionHtml):
            return answerLine[len(questionHtml):].strip()
        return answerLine

    def templateBrowserColumn(self):
        return self.template().getName()

    def nextDue(self):
        if self.odid:
            return _("(filtered)")
        elif self.queue == QUEUE_LRN:
            date = self.due
        elif self.queue == QUEUE_NEW or self.type == CARD_NEW:
            return str(self.due)
        elif self.queue in (QUEUE_REV, QUEUE_DAY_LRN) or (self.type == CARD_DUE and
                                                          self.queue < 0#suspended or buried
        ):
            date = time.time() + ((self.due - self.col.sched.today)*86400)
        else:
            return ""
        return formatDay(date)

    def dueBrowserColumn(self):
        # catch invalid dates
        try:
            dueString = self.nextDue()
        except:
            dueString = ""
        if self.queue < 0:#supsended or buried
            dueString = "(" + dueString + ")"
        return dueString

    def repsBrowserColumn(self):
        return str(self.reps)

    def lapsesBrowserColumn(self):
        return str(self.lapses)

    def ivlBrowserColumn(self):
        if self.type == 0:
            return _("(new)")
        elif self.type == 1:
            return _("(learning)")
        return fmtTimeSpan(self.ivl*86400)

    def easeBrowserColumn(self):
        if self.type == 0:
            return _("(new)")
        return "%d%%" % (self.factor/10)

    def deckBrowserColumn(self):
        if self.odid:
            # in a cram deck
            return "%s (%s)" % (
                self.col.decks.name(self.did),
                self.col.decks.name(self.odid))
        # normal deck
        return self.col.decks.name(self.did)

    def overdueIvlBrowserColumn(self):
        if self.odid or self.queue == QUEUE_LRN:
            return
        elif self.queue == QUEUE_NEW or self.type == CARD_NEW:
            return
        elif self.queue in (QUEUE_REV, QUEUE_DAY_LRN) or (self.type == CARD_DUE and self.queue < 0):
            lateness = self.col.sched.today - self.due
            if lateness > 0 :
                return f"{lateness} day{'s' if lateness > 1 else ''}"
            else:
                return

    def previousIvlBrowserColumn(self):

        ivl = self.col.db.scalar(
            "select ivl from revlog where cid = ? "
            "order by id desc limit 1 offset 1", self.id)
        if ivl is None:
            return
        elif ivl == 0:
            return "0 days"
        elif ivl > 0:
            return fmtTimeSpan(ivl*86400)
        else:
            return timeFmt(-ivl)

    def percentCorrectBrowserColumn(self):
        if self.reps <= 0:
            return ""
        return "{:2.0f}%".format(100 - ((self.lapses / float(self.reps)) * 100)),

    def previousDurationBrowserColumn(self):
        return timeFmt(self.col.db.scalar(
            "select time/1000.0 from revlog where cid = ? "
            "order by id desc limit 1", self.id))

    def toTup(card, params, nidToRand):
        """A tuple to sort the card. See bothSched.sortCids to get more
        informations."""
        l = []
        for param in params:
            if isinstance(param, tuple):
                param, reverse = param
            else:
                reverse = False
            if param == "new first":
                val = (isNotNew(card.note()))#false occurs first in list
            elif param == "seen first":
                val = (isNew(card.note()))#false occurs first in list
            elif param in {"ord", "card position"}:
                val = (card.ord)
            elif param == "note creation":
                val = (card.nid)
            elif param == "card creation":
                val = (card.id)
            elif param == "mod":
                val = (card.note().mod)
            elif param == "note random":
                if card.nid not in nidToRand:
                    nidToRand[card.nid] = random()
                val = nidToRand[card.nid]
            elif param == "card random":
                val = (random())
            if reverse:
                val = -val
            l.append(val)
        return tuple(l)
