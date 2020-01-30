# Copyright: Ankitects Pty Ltd and contributors
# -*- coding: utf-8 -*-
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
from operator import itemgetter

import aqt
from anki.consts import NEW_CARDS_RANDOM
from anki.lang import _, ngettext
from aqt.qt import *
from aqt.utils import (askUser, getOnlyText, openHelp, restoreGeom, saveGeom,
                       showInfo, showWarning, tooltip)


class DeckConf(QDialog):
    def __init__(self, mw, deck):
        QDialog.__init__(self, mw)
        self.mw = mw
        self.deck = deck
        self.childDids = [
            deck[1] for deck in self.mw.col.decks.children(self.deck['id'])]
        self._origNewOrder = None
        self.form = aqt.forms.dconf.Ui_Dialog()
        self.form.setupUi(self)
        self.mw.checkpoint(_("Options"))
        self.setupCombos()
        self.setupConfs()
        self.setWindowModality(Qt.WindowModal)
        self.form.buttonBox.helpRequested.connect(lambda: openHelp("deckoptions"))
        self.form.confOpts.clicked.connect(self.confOpts)
        self.form.buttonBox.button(QDialogButtonBox.RestoreDefaults).clicked.connect(self.onRestore)
        self.setWindowTitle(_("Options for %s") % self.deck['name'])
        # qt doesn't size properly with altered fonts otherwise
        restoreGeom(self, "deckconf", adjustSize=True)
        self.show()
        self.exec_()
        saveGeom(self, "deckconf")

    def setupCombos(self):
        import anki.consts as cs
        self.form.newOrder.addItems(list(cs.newCardOrderLabels().values()))
        self.form.newOrder.currentIndexChanged.connect(self.onNewOrderChanged)

    # Conf list
    ######################################################################

    def setupConfs(self):
        self.form.dconf.currentIndexChanged.connect(self.onConfChange)
        self.conf = None
        self.loadConfs()

    def loadConfs(self):
        current = self.deck['conf']
        self.confList = self.mw.col.decks.allConf()
        self.confList.sort(key=itemgetter('name'))
        startOn = 0
        self.ignoreConfChange = True
        self.form.dconf.clear()
        for idx, conf in enumerate(self.confList):
            self.form.dconf.addItem(conf['name'])
            if str(conf['id']) == str(current):
                startOn = idx
        self.ignoreConfChange = False
        self.form.dconf.setCurrentIndex(startOn)
        if self._origNewOrder is None:
            self._origNewOrder =  self.confList[startOn]['new']['order']
        self.onConfChange(startOn)

    def confOpts(self):
        menu = QMenu(self.mw)
        a = menu.addAction(_("Add"))
        a.triggered.connect(self.addGroup)
        a = menu.addAction(_("Delete"))
        a.triggered.connect(self.remGroup)
        a = menu.addAction(_("Rename"))
        a.triggered.connect(self.renameGroup)
        a = menu.addAction(_("Set for all subdecks"))
        a.triggered.connect(self.setChildren)
        if not self.childDids:
            a.setEnabled(False)
        menu.exec_(QCursor.pos())

    def onConfChange(self, idx):
        if self.ignoreConfChange:
            return
        if self.conf:
            self.saveConf()
        conf = self.confList[idx]
        self.deck['conf'] = conf['id']
        self.loadConf()
        cnt = 0
        for deck in self.mw.col.decks.all():
            if deck['dyn']:
                continue
            if deck['conf'] == conf['id']:
                cnt += 1
        if cnt > 1:
            txt = _("Your changes will affect multiple decks. If you wish to "
            "change only the current deck, please add a new options group first.")
        else:
            txt = ""
        self.form.count.setText(txt)

    def addGroup(self):
        name = getOnlyText(_("New options group name:"))
        if not name:
            return
        # first, save currently entered data to current conf
        self.saveConf()
        # then clone the conf
        id = self.mw.col.decks.confId(name, cloneFrom=self.conf)
        # set the deck to the new conf
        self.deck['conf'] = id
        # then reload the conf list
        self.loadConfs()

    def remGroup(self):
        if int(self.conf['id']) == 1:
            showInfo(_("The default configuration can't be removed."), self)
        else:
            self.mw.col.decks.remConf(self.conf['id'])
            self.deck['conf'] = 1
            self.loadConfs()

    def renameGroup(self):
        old = self.conf['name']
        name = getOnlyText(_("New name:"), default=old)
        if not name or name == old:
            return
        self.conf['name'] = name
        self.loadConfs()

    def setChildren(self):
        if not askUser(
            _("Set all decks below %s to this option group?") %
            self.deck['name']):
            return
        for did in self.childDids:
            deck = self.mw.col.decks.get(did)
            if deck['dyn']:
                continue
            deck['conf'] = self.deck['conf']
            self.mw.col.decks.save(deck)
        tooltip(ngettext("%d deck updated.", "%d decks updated.", \
                        len(self.childDids)) % len(self.childDids))

    # Loading
    ##################################################

    def listToUser(self, l):
        return " ".join([str(x) for x in l])

    def parentLimText(self, type="new"):
        # top level?
        if "::" not in self.deck['name']:
            return ""
        lim = -1
        for deck in self.mw.col.decks.parents(self.deck['id']):
            conf = self.mw.col.decks.confForDid(deck['id'])
            x = conf[type]['perDay']
            if lim == -1:
                lim = x
            else:
                lim = min(x, lim)
        return _("(parent limit: %d)") % lim

    def loadConf(self):
        self.conf = self.mw.col.decks.confForDid(self.deck['id'])
        # new
        conf = self.conf['new']
        self.form.lrnSteps.setText(self.listToUser(conf['delays']))
        self.form.lrnGradInt.setValue(conf['ints'][0])
        self.form.lrnEasyInt.setValue(conf['ints'][1])
        self.form.lrnEasyInt.setValue(conf['ints'][1])
        self.form.lrnFactor.setValue(conf['initialFactor']/10.0)
        self.form.newOrder.setCurrentIndex(conf['order'])
        self.form.newPerDay.setValue(conf['perDay'])
        self.form.bury.setChecked(conf.get("bury", True))
        self.form.newplim.setText(self.parentLimText('new'))
        # rev
        conf = self.conf['rev']
        self.form.revPerDay.setValue(conf['perDay'])
        self.form.easyBonus.setValue(conf['ease4']*100)
        self.form.fi1.setValue(conf['ivlFct']*100)
        self.form.maxIvl.setValue(conf['maxIvl'])
        self.form.revplim.setText(self.parentLimText('rev'))
        self.form.buryRev.setChecked(conf.get("bury", True))
        self.form.hardFactor.setValue(int(conf.get("hardFactor", 1.2)*100))
        if self.mw.col.schedVer() == 1:
            self.form.hardFactor.setVisible(False)
            self.form.hardFactorLabel.setVisible(False)
        # lapse
        conf = self.conf['lapse']
        self.form.lapSteps.setText(self.listToUser(conf['delays']))
        self.form.lapMult.setValue(conf['mult']*100)
        self.form.lapMinInt.setValue(conf['minInt'])
        self.form.leechThreshold.setValue(conf['leechFails'])
        self.form.leechAction.setCurrentIndex(conf['leechAction'])
        # general
        conf = self.conf
        self.form.maxTaken.setValue(conf['maxTaken'])
        self.form.showTimer.setChecked(conf.get('timer', 0))
        self.form.autoplaySounds.setChecked(conf['autoplay'])
        self.form.replayQuestion.setChecked(conf.get('replayq', True))
        # description
        self.form.desc.setPlainText(self.deck['desc'])

    def onRestore(self):
        self.mw.progress.start()
        self.mw.col.decks.restoreToDefault(self.conf)
        self.mw.progress.finish()
        self.loadConf()

    # New order
    ##################################################

    def onNewOrderChanged(self, new):
        old = self.conf['new']['order']
        if old == new:
            return
        self.conf['new']['order'] = new
        self.mw.progress.start()
        self.mw.col.sched.resortConf(self.conf)
        self.mw.progress.finish()

    # Saving
    ##################################################

    def updateList(self, conf, key, w, minSize=1):
        items = str(w.text()).split(" ")
        ret = []
        for i in items:
            if not i:
                continue
            try:
                i = float(i)
                assert i > 0
                if i == int(i):
                    i = int(i)
                ret.append(i)
            except:
                # invalid, don't update
                showWarning(_("Steps must be numbers."))
                return
        if len(ret) < minSize:
            showWarning(_("At least one step is required."))
            return
        conf[key] = ret

    def saveConf(self):
        # new
        conf = self.conf['new']
        self.updateList(conf, 'delays', self.form.lrnSteps)
        conf['ints'][0] = self.form.lrnGradInt.value()
        conf['ints'][1] = self.form.lrnEasyInt.value()
        conf['initialFactor'] = self.form.lrnFactor.value()*10
        conf['order'] = self.form.newOrder.currentIndex()
        conf['perDay'] = self.form.newPerDay.value()
        conf['bury'] = self.form.bury.isChecked()
        if self._origNewOrder != conf['order']:
            # order of current deck has changed, so have to resort
            if conf['order'] == NEW_CARDS_RANDOM:
                self.mw.col.sched.randomizeCards(self.deck['id'])
            else:
                self.mw.col.sched.orderCards(self.deck['id'])
        # rev
        conf = self.conf['rev']
        conf['perDay'] = self.form.revPerDay.value()
        conf['ease4'] = self.form.easyBonus.value()/100.0
        conf['ivlFct'] = self.form.fi1.value()/100.0
        conf['maxIvl'] = self.form.maxIvl.value()
        conf['bury'] = self.form.buryRev.isChecked()
        conf['hardFactor'] = self.form.hardFactor.value()/100.0
        # lapse
        conf = self.conf['lapse']
        self.updateList(conf, 'delays', self.form.lapSteps, minSize=0)
        conf['mult'] = self.form.lapMult.value()/100.0
        conf['minInt'] = self.form.lapMinInt.value()
        conf['leechFails'] = self.form.leechThreshold.value()
        conf['leechAction'] = self.form.leechAction.currentIndex()
        # general
        conf = self.conf
        conf['maxTaken'] = self.form.maxTaken.value()
        conf['timer'] = self.form.showTimer.isChecked() and 1 or 0
        conf['autoplay'] = self.form.autoplaySounds.isChecked()
        conf['replayq'] = self.form.replayQuestion.isChecked()
        # description
        self.deck['desc'] = self.form.desc.toPlainText()
        self.mw.col.decks.save(self.deck)
        self.mw.col.decks.save(self.conf)

    def reject(self):
        self.accept()

    def accept(self):
        self.saveConf()
        self.mw.reset()
        QDialog.accept(self)
