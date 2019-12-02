import copy
import re
import time

from anki.consts import *
from anki.fields import Field
from anki.lang import _
from anki.templates import Template, defaultTemplate
from anki.utils import (DictAugmentedIdUsn, checksum, ids2str, intTime,
                        joinFields, splitFields)

defaultModel = {
    'sortf': 0,
    'did': 1,
    'latexPre': """\
\\documentclass[12pt]{article}
\\special{papersize=3in,5in}
\\usepackage[utf8]{inputenc}
\\usepackage{amssymb,amsmath}
\\pagestyle{empty}
\\setlength{\\parindent}{0in}
\\begin{document}
""",
    'latexPost': "\\end{document}",
    'mod': 0,
    'usn': 0,
    'vers': [], # FIXME: remove when other clients have caught up
    'type': MODEL_STD,
    'css': """\
.card {
 font-family: arial;
 font-size: 20px;
 text-align: center;
 color: black;
 background-color: white;
}
"""
}

class Model(DictAugmentedIdUsn):
    def load(self, manager, dict):
        super().load(manager, dict)
        self['tmpls'] = list(map(lambda tmpl: Template(self, tmpl), self['tmpls']))
        self['flds'] = list(map(lambda fld: Field(self, fld), self['flds']))

    def new(self, manager, name):
        assert(isinstance(name, str))
        model = defaultModel.copy()
        model['name'] = name
        model['mod'] = intTime()
        model['flds'] = []
        model['tmpls'] = []
        model['tags'] = []
        model['id'] = None
        self.load(manager, model)

    def save(self, templates=False, recomputeReq=True):
        """
        * Mark model modified.
        Keyword arguments:
        model -- A Model
        recomputeReq -- whether to update requirements. Usually, you want to; however, if we changed only something such as the font or stickyness, it's useless.
        templates -- whether to check for cards not generated in this model. It's not done if requirement is not updated, as it would be useless.
        """
        if self.getId():
            if recomputeReq:
                self._updateRequired()
                if templates:
                    self._syncTemplates()
        super().save()

    def add(self):
        """Add a new model model in the database of models"""
        self._setID()
        self.update()
        self.setCurrent()
        self.save()

    def update(self):
        "Add or update an existing model. Used for syncing and merging."
        self.ensureNameUnique()
        self.manager.models[str(self.getId())] = self
        # mark registry changed, but don't bump mod time
        self.manager.save()

    def setCurrent(self):
        """Change curModel value and marks the collection as modified."""
        self.manager.col.conf['curModel'] = self.getId()
        self.manager.col.setMod()

    def rem(self):
        "Delete model, and all its cards/notes."
        self.manager.col.modSchema(check=True)
        current = self.manager.current().getId() == self.getId()
        # delete notes/cards
        self.manager.col.remCards(self.manager.col.db.list("""
select id from cards where nid in (select id from notes where mid = ?)""",
                                      self.getId()))
        # then the model
        del self.manager.models[str(self.getId())]
        self.manager.save()
        # GUI should ensure last model is not deleted
        if current:
            list(self.manager.models.values())[0].setCurrent()

    def ensureNameUnique(self):
        """Transform the name of model into a new name.
        If a model with this name but a distinct id exists in the
        manager, the name of this object is appended by - and by a
        5 random digits generated using the current time.
        Keyword arguments"""
        for mcur in self.manager.all():
            if (mcur.getName() == self.getName() and mcur.getId() != self.getId()):
                self.setName(self.getName() + "-" + checksum(str(time.time()))[:5])
                break

    def _setID(self):
        """Set the id of model to a new unique value."""
        while 1:
            id = str(intTime(1000))
            if id not in self.manager.models:
                break
        self['id'] = id

    # Tools
    ##################################################

    def nids(self):
        """The ids of notes whose model is model.
        Keyword arguments
        model -- a model object."""
        return self.manager.col.db.list(
            "select id from notes where mid = ?", self.getId())

    def deepcopy(self):
        dict = {}
        for key in self:
            if key in {'tmpls', 'flds'}:
                image = list(map(lambda object: object.deepcopy(), self[key]))
            else:
                image = copy.deepcopy(self[key])
            dict[key] = image
        return self.__class__(self.manager, dict=dict)

    def copyInCol(self, col):
        m2 = self.deepcopy()
        m2.manager = col.models
        m2.update()
        return m2

    def copy_(self):
        "A copy of model, already in the collection."
        # copy_ instead of copy; because it seems to override
        # dictionnary's copy method, which seems to be used somewhere
        m2 = self.deepcopy()
        m2['name'] = _("%s copy") % m2.getName()
        self.add()
        return m2

    def useCount(self):
        """Number of note using the model model.
        Keyword arguments
        model -- a model object."""
        return self.manager.col.db.scalar(
            "select count() from notes where mid = ?", self.getId())

    # Fields
    ##################################################

    def fieldMap(self):
        """Mapping of (field name) -> (ord, field object).
        keyword arguments:
        model : a model
        """
        return dict((fieldType.getName(), (fieldType['ord'], fieldType)) for fieldType in self['flds'])

    def fieldNames(self):
        """The list of names of fields of this model."""
        return [fieldType.getName() for fieldType in self['flds']]

    def sortIdx(self):
        """The index of the field used for sorting."""
        return self['sortf']

    def setSortIdx(self, idx):
        """State that the id of the sorting field of the model is idx.
        Mark the model as modified, change the cache.
        Keyword arguments
        idx -- the identifier of a field
        """
        assert 0 <= idx < len(self['flds'])
        self.manager.col.modSchema(check=True)
        self['sortf'] = idx
        self.manager.col.updateFieldCache(self.nids())
        self.save(recomputeReq=False)

    def _transformFields(self, fn):
        """For each note of the model self, apply fn to the set of field's
        value, and save the note modified.
        fn -- a function taking and returning a list of field.
        """
        # model hasn't been added yet?
        if not self.getId():
            return
        notesUpdates = [(joinFields(fn(splitFields(flds))),
                         intTime(),
                         self.manager.col.usn(),
                         id)
                        for (id, flds) in
                        self.manager.col.db.execute("select id, flds from notes where mid = ?", self.getId())
        ]
        self.manager.col.db.executemany(
            "update notes set flds=?,mod=?,usn=? where id = ?", notesUpdates)

    def _updateFieldOrds(self):
        """
        Change the order of the field of the model in order to copy
        the order in model['flds'].
        Keyword arguments
        model -- a model"""
        for index, fieldType in enumerate(self['flds']):
            fieldType['ord'] = index

    # Templates
    ##################################################

    def newTemplate(self, name):
        """A new template, whose content is the one of
        defaultTemplate, and name is name.

        It's used in order to import mnemosyn, and create the standard
        model during anki's first initialization. It's not used in day to day anki.
        """
        return Template(self, name=name)

    def _updateTemplOrds(self):
        """Change the value of 'ord' in each template of this model to reflect its new position"""
        for index, template in enumerate(self['tmpls']):
            template['ord'] = index

    def _syncTemplates(self, changedOrNewReq=None):
        """Generate all cards not yet generated, whose note's model is model.

         It's called only when model is saved, a new model is given
        and template is asked to be computed

        changedOrNewReq -- set of index of templates which needs to be
        recomputed

        """
        rem = self.manager.col.genCards(self.nids(), changedOrNewReq)

    def newField(self, name):
        return Field(self, name=name)

    def getTemplate(self, ord=0):
        """Template at position ord. A cloze template for {{c7 (called by
        getTemplate(6)) would return a copy of the unique template,
        where "ord" is set to 6.

        """
        if self.isStd() or ord==0:
            return self['tmpls'][ord]
        else:
            template = self['tmpls'][0]
            template = template.deepcopy()
            template['ord'] = ord
            template['name'] = _("Card %d") % (ord+1)
            return template

    # Model changing
    ##########################################################################
    # - maps are ord->ord, and there should not be duplicate targets
    # - newModel should be self if model is not changing

    def change(self, oldModel, nids, fmap, cmap):
        """Change the model of the nodes in nids to self
        currently, fmap and cmap are null only for tests.
        keyword arguments
        oldModel -- the previous oldModel of the notes
        nids -- a list of id of notes whose oldModel is oldModel
        self -- the model to which the cards must be converted
        fmap -- the dictionnary sending to each fields'ord of the old model a field'ord of the new model
        cmap -- the dictionnary sending to each card type's ord of the old model a card type's ord of the new model
        """
        # changeNote does not uses oldModel, so self has been chosen to be the new model.
        self.manager.col.modSchema(check=True)
        assert self.getId() == oldModel.getId() or (fmap and cmap)
        assert not self.manager.col.db.list("select id from notes where mid <> ? and id in "+ids2str(nids), oldModel.getId())
        if fmap:
            self._changeNotes(nids, fmap)
        if cmap:
            self._changeCards(nids, oldModel, cmap)
        self.manager.col.genCards(nids)

    def _changeNotes(self, nids, map):
        """Change the note whose ids are nid to the model self, reorder
        fields according to map. Write the change in the database
        Note that if a field is mapped to nothing, it is lost
        keyword arguments:
        nids -- the list of id of notes to change
        newmodel -- the model of destination of the note
        map -- the dictionnary sending to each fields'ord of the old model a field'ord of the new model
        """
        noteData = []
        #The list of dictionnaries, containing the information relating to the new cards
        nfields = len(self['flds'])
        for (nid, flds) in self.manager.col.db.execute(
            "select id, flds from notes where id in "+ids2str(nids)):
            newflds = {}
            flds = splitFields(flds)
            for old, new in list(map.items()):
                newflds[new] = flds[old]
            flds = [newflds.get(index, "")
                    for index in range(nfields)]
            flds = joinFields(flds)
            noteData.append(dict(nid=nid, flds=flds, mid=self.getId(),
                      mod=intTime(),usn=self.manager.col.usn()))
        self.manager.col.db.executemany(
            "update notes set flds=:flds,mid=:mid,mod=:mod,usn=:usn where id = :nid", noteData)
        self.manager.col.updateFieldCache(nids)

    def _changeCards(self, nids, oldModel, map):
        """Change the note whose ids are nid to the model self, reorder
        fields according to map. Write the change in the database
        Remove the cards mapped to nothing
        If the source is a cloze, it is (currently?) mapped to the
        card of same order in self, independtly of map.
        keyword arguments:
        nids -- the list of id of notes to change
        oldModel -- the soruce model of the notes
        newmodel -- the model of destination of the notes
        map -- the dictionnary sending to each card 'ord of the old model a card'ord of the new model or to None
        """
        cardData = []
        deleted = []
        for (cid, ord) in self.manager.col.db.execute(
            "select id, ord from cards where nid in "+ids2str(nids)):
            # if the src model is a cloze, we ignore the map, as the gui
            # doesn't currently support mapping them
            if oldModel.isCloze():
                new = ord
                if self['type'] != MODEL_CLOZE:
                    # if we're mapping to a regular note, we need to check if
                    # the destination ord is valid
                    if len(self['tmpls']) <= ord:
                        new = None
            else:
                # mapping from a regular note, so the map should be valid
                new = map[ord]
            if new is not None:
                cardData.append(dict(
                    cid=cid,new=new,usn=self.manager.col.usn(),mod=intTime()))
            else:
                deleted.append(cid)
        self.manager.col.db.executemany(
            "update cards set ord=:new,usn=:usn,mod=:mod where id=:cid",
            cardData)
        self.manager.col.remCards(deleted)

    # Schema hash
    ##########################################################################

    def scmhash(self):
        """Return a hash of the schema, to see if models are
        compatible. Consider only name of fields and of card type, and
        not the card type itself.
        """
        scm = ""
        for fieldType in self['flds']:
            scm += fieldType.getName()
        for template in self['tmpls']:
            scm += template.getName()
        return checksum(scm)

    # Required field/text cache
    ##########################################################################

    def _updateRequired(self):
        """Entirely recompute the model's req value

        Return positions idx such that the card type is new or has its
        question changed if its a standard model
        """
        if self.isCloze():
            # nothing to do
            return
        req = []
        changedOrNewReq = set()
        flds = [fieldType.getName() for fieldType in self['flds']]
        for idx, template in enumerate(self['tmpls']):
            if (hasattr(template, 'old_req') and
                hasattr(template, 'old_type') and
                hasattr(template, 'old_qfmt') and
                template.old_qfmt == template['qfmt']):
                req.append((idx, template.old_type, template.old_req))
                if getattr(template, "is_new", True):
                    changedOrNewReq.add(idx)
            else:
                ret = template._req(flds)
                req.append((idx, ret[0], ret[1]))
                changedOrNewReq.add(idx)
        self['req'] = req
        return changedOrNewReq

    # Required field/text cache
    ##########################################################################

    def availOrds(self, flds, changedOrNewReq=None):
        """Given a joined field string, return ordinal of card type which
        should be generated. See
        ../documentation/templates_generation_rules.md for the detail
        """
        if self.manager.col.conf.get("complexTemplates", False):
            return self._availOrdsReal(flds, changedOrNewReq)
        else:
            return self._availOrdsOriginal(flds, changedOrNewReq)

    def _availOrdsReal(self, flds, changedOrNewReq):
        """
        self -- model manager
        model -- a model object
        """
        available = []
        flist = splitFields(flds)
        fields = {} #
        for (name, (idx, conf)) in list(self.fieldMap().items()):#conf is not used
            fields[name] = flist[idx]
        if self.isCloze():
            potentialOrds = self._availClozeOrds(flds)
        else:
            potentialOrds = changedOrNewReq if changedOrNewReq is not None else range(len(self["tmpls"]))
        for ord in potentialOrds:
            template = self["tmpls"][ord]
            format = template['qfmt']
            html, showAField = anki.template.renderAndIsFieldPresent(format, fields) #replace everything of the form {{ by its value TODO check
            if showAField:
                available.append(ord)
        return available

    def _availOrdsOriginal(self, flds, changedOrNewReq):
        """Given a joined field string, return ordinal of card type which
        should be generated. See
        ../documentation/templates_generation_rules.md for the detail

        """
        if self.isCloze():
            return self._availClozeOrds(flds)
        fields = {}
        for index, fieldType in enumerate(splitFields(flds)):
            fields[index] = fieldType.strip()
        avail = []#List of ord cards which would be generated
        ords = changedOrNewReq if changedOrNewReq is not None else range(len(self['req']))
        for ord in ords:
            ord, type, req = self['req'][ord]
            # unsatisfiable template
            if type == "none":
                continue
            # AND requirement?
            elif type == "all":
                ok = True
                for idx in req:
                    if not fields[idx]:
                        # missing and was required
                        ok = False
                        break
                if not ok:
                    continue
            # OR requirement?
            elif type == "any":
                ok = False
                for idx in req:
                    if fields[idx]:
                        ok = True
                        break
                if not ok:
                    continue
            avail.append(ord)
        return avail

    def _availClozeOrds(self, flds, allowEmpty=True, onlyFirst=False):
        """The list of fields F which are used in some {{cloze:F}} in a template
        keyword arguments:
        flds: a list of fields as in the database
        allowEmpty: allows to treat a note without cloze field as a note with a cloze number 1
        onlyFirst -- return a list with one element. Usefull when we only want to test emptyness.
        """
        sflds = splitFields(flds)
        map = self.fieldMap()
        ords = set()
        matches = re.findall("{{[^}]*?cloze:(?:[^}]?:)*(.+?)}}", self.getTemplate()['qfmt'])
        matches += re.findall("<%cloze:(.+?)%>", self.getTemplate()['qfmt'])
        for fname in matches:
            if fname not in map:
                continue#Do not consider cloze not related to an existing field
            ord = map[fname][0]
            matches = re.findall(r"(?s){{c(\d+)::.+?}}", sflds[ord])
            if onlyFirst:
                for match in matches:
                    if int(match) != 0:
                        return [int(match)-1]
            else:
                ords.update([int(match)-1 for match in matches if int(match) != 0])#The number of the cloze of this field, minus one
        if not ords and allowEmpty:
            # empty clozes use first ord
            return [0]
        l = list(ords)
        l.sort()
        return l

    def isCloze(self):
        return self['type'] == MODEL_CLOZE

    def isStd(self):
        return self['type'] == MODEL_STD

    def _addTmp(self):
        self.fieldNameToOrd = {}
        self.templateNameToOrd = {}
        for field in self['flds']:
            name = field['name']
            ord = field['ord']
            self.fieldNameToOrd[name] = ord
        for idx, template in enumerate(self['tmpls']):
            name = template['name']
            ord = template['ord']
            self.templateNameToOrd[name] = ord
            if self['type'] == MODEL_STD:
                template.old_type = self['req'][idx][1]
                template.old_req = self['req'][idx][2]
            template.old_qfmt = template['qfmt']
            template.is_new = False
