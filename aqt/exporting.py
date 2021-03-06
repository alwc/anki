# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""
mw -- the main window
col -- the collection
frm -- the formula GUIn
exporters -- A list of pairs (description of an exporter class, the class)
exporter -- An instance of the class choosen in the GUI
decks -- The list of decks option used in the GUI. All Decks and decks' name
isApkg -- Whether exporter's suffix is apkg
isVerbatim -- Whether exporter has an attribute "verbatim" set to True. Occurs only in Collection package exporter.
isTextNote -- Whether exporter has an attribute "includeTags" set to True. Occurs only in textNoteExporter.
"""

import os
import re
import time

import aqt
from anki.exporting import exporters
from anki.hooks import addHook, remHook
from anki.lang import _, ngettext
from aqt.qt import *
from aqt.utils import (checkInvalidFilename, getSaveFile, showInfo,
                       showWarning, tooltip)


class ExportDialog(QDialog):

    def __init__(self, mw, deck=None, cids=None):
        """
        cids -- the cards selected, if it's opened from the browser
        """
        QDialog.__init__(self, mw, Qt.Window)
        self.mw = mw
        self.cids = cids
        self.col = mw.col
        self.frm = aqt.forms.exporting.Ui_ExportDialog()
        self.frm.setupUi(self)
        self.exporter = None
        self.setup(deck, cids)
        self.exec_()

    def setup(self, deck=None, cids=None):
        """

        keyword arguments:
        deck -- if None, then export whole anki. If deck, export this deck (at least as default).
        cids -- If cids is not None, export those cards.
        """
        self.exporters = exporters()
        # if a deck specified, start with .apkg type selected
        idx = 0
        if deck or cids:
            for index, (exporterString,e) in enumerate(self.exporters):
                if e.ext == ".apkg":
                    idx = index
                    break
        self.frm.format.insertItems(0, [e[0] for e in self.exporters])
        self.frm.format.setCurrentIndex(idx)
        self.frm.format.activated.connect(self.exporterChanged)
        self.exporterChanged(idx)
        # deck list
        self.decks = [_("All Decks")]
        if cids:
            bs=_("Browser's selection")
            self.decks = [bs] + self.decks
        self.decks = self.decks + sorted(self.col.decks.allNames())
        self.frm.deck.addItems(self.decks)
        # save button
        exportButton = QPushButton(_("Export..."))
        self.frm.buttonBox.addButton(exportButton, QDialogButtonBox.AcceptRole)
        # set default option if accessed through deck button
        if deck:
            name = deck.getName()
            index = self.frm.deck.findText(name)
            self.frm.deck.setCurrentIndex(index)

    def exporterChanged(self, idx):
        self.exporter = self.exporters[idx][1](self.col)
        self.isApkg = self.exporter.ext == ".apkg"
        self.isVerbatim = getattr(self.exporter, "verbatim", False)
        self.isTextNote = hasattr(self.exporter, "includeTags")
        self.frm.includeSched.setVisible(
            getattr(self.exporter, "includeSched", None) is not None)
        self.frm.includeMedia.setVisible(
            getattr(self.exporter, "includeMedia", None) is not None)
        self.frm.includeTags.setVisible(
            getattr(self.exporter, "includeTags", None) is not None)
        html = getattr(self.exporter, "includeHTML", None)
        if html is not None:
            self.frm.includeHTML.setVisible(True)
            self.frm.includeHTML.setChecked(html)
        else:
            self.frm.includeHTML.setVisible(False)
        # show deck list?
        self.frm.deck.setVisible(not self.isVerbatim)

    def accept(self):
        self.exporter.includeSched = (
            self.frm.includeSched.isChecked())
        self.exporter.includeMedia = (
            self.frm.includeMedia.isChecked())
        self.exporter.includeTags = (
            self.frm.includeTags.isChecked())
        self.exporter.includeHTML = (
            self.frm.includeHTML.isChecked())
        if self.frm.deck.currentIndex() == 1: #position 1 means: all decks.
            self.exporter.did = None
            self.exporter.cids = None
        elif self.frm.deck.currentIndex() == 0 and self.cids is not None:#position 0 means: selected decks.
            self.exporter.did = None
            self.exporter.cids = self.cids
        else:
            self.exporter.cids = None
            name = self.decks[self.frm.deck.currentIndex()]
            self.exporter.did = self.col.decks.id(name)
        if self.isVerbatim:
            name = time.strftime("-%Y-%m-%d@%H-%M-%S",
                                 time.localtime(time.time()))
            deck_name = _("collection")+name
        else:
            # Get deck name and remove invalid filename characters
            deck_name = self.decks[self.frm.deck.currentIndex()]
            deck_name = re.sub('[\\\\/?<>:*|"^]', '_', deck_name)

        if not self.isVerbatim and self.isApkg and self.exporter.includeSched and self.col.schedVer() == 2:
            showInfo("Please switch to the regular scheduler before exporting a single deck .apkg with scheduling.")
            return

        filename = '{0}{1}'.format(deck_name, self.exporter.ext)
        while 1:
            fileName = getSaveFile(self, _("Export"), "export",
                               self.exporter.key, self.exporter.ext,
                               fname=filename)
            if not fileName:
                return
            if checkInvalidFilename(os.path.basename(fileName), dirsep=False):
                continue
            break
        self.hide()
        if fileName:
            self.mw.progress.start(immediate=True)
            try:
                file = open(fileName, "wb")
                file.close()
            except (OSError, IOError) as e:
                showWarning(_("Couldn't save file: %s") % str(e))
            else:
                os.unlink(fileName)
                exportedMedia = lambda cnt: self.mw.progress.update(
                        label=ngettext("Exported %d media file",
                                       "Exported %d media files", cnt) % cnt
                        )
                addHook("exportedMediaFiles", exportedMedia)
                self.exporter.exportInto(fileName)
                remHook("exportedMediaFiles", exportedMedia)
                period = 3000
                if self.isVerbatim:
                    msg = _("Collection exported.")
                else:
                    if self.isTextNote:
                        msg = ngettext("%d note exported.", "%d notes exported.",
                                    self.exporter.count) % self.exporter.count
                    else:
                        msg = ngettext("%d card exported.", "%d cards exported.",
                                    self.exporter.count) % self.exporter.count
                tooltip(msg, period=period)
            finally:
                self.mw.progress.finish()
        QDialog.accept(self)
