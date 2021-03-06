# Copyright: Ankitects Pty Ltd and contributors
# -*- coding: utf-8 -*-
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

import faulthandler
import gc
import platform
import re
import signal
import sys
import time
import traceback
import zipfile
from threading import Thread
from typing import Optional

from send2trash import send2trash

import anki.sound
import aqt
import aqt.decks
import aqt.mediasrv
import aqt.progress
import aqt.stats
import aqt.toolbar
import aqt.webview
from anki import Collection
from anki.collection import _Collection
from anki.hooks import addHook, runFilter, runHook
from anki.lang import _, ngettext
from anki.storage import Collection
from anki.utils import devMode, ids2str, intTime, isMac, isWin, splitFields
from aqt.qt import *
from aqt.qt import sip
from aqt.utils import (askUser, checkInvalidFilename, getFile, getOnlyText,
                       getText, openHelp, openLink, restoreGeom, restoreState,
                       saveGeom, showInfo, showText, showWarning, tooltip)


"""
self.stateShortcuts -- the list of QShortcut elements due to the actual state of the main window (i.e. reviewer or overwiew).
"""

class AnkiQt(QMainWindow):
    """
    col -- The collection
    state -- It's states which kind of content main shows. Either:
      -- startup
      -- resetRequired: during review, when edit or browser is opened, the window show "waiting for editing to finish. Resume now
      -- sync
      -- overview
      -- review
      -- profileManager
      -- deckBrowser
    stateShortcuts -- shortcuts related to the kind of window currently in main.
    bottomWeb -- a ankiwebview, with the bottom of the main window. Shown unless for reset required.
    app -- an object of class AnkiApp.
    """

    def __init__(self, app, profileManager, opts, args):
        QMainWindow.__init__(self)
        self.state = "startup"
        self.opts = opts
        self.col: Optional[_Collection] = None
        aqt.mw = self
        self.app = app
        self.pm = profileManager
        # init rest of app
        self.safeMode = self.app.queryKeyboardModifiers() & Qt.ShiftModifier
        try:
            self.setupUI()
            self.setupAddons()
        except:
            showInfo(_("Error during startup:\n%s") % traceback.format_exc())
            sys.exit(1)
        # must call this after ui set up
        if self.safeMode:
            tooltip(_("Shift key was held down. Skipping automatic "
                    "syncing and add-on loading."))
        # were we given a file to import?
        if args and args[0]:
            self.onAppMsg(args[0])
       # Load profile in a timer so we can let the window finish init and not
        # close on profile load error.
        if isWin:
            fn = self.setupProfileAfterWebviewsLoaded
        else:
            fn = self.setupProfile
        self.progress.timer(10, fn, False, requiresCollection=False)

    def setupUI(self):
        self.col = None
        self.setupCrashLog()
        self.disableGC()
        self.setupAppMsg()
        self.setupKeys()
        self.setupThreads()
        self.setupMediaServer()
        self.setupSound()
        self.setupSpellCheck()
        self.setupMainWindow()
        self.setupSystemSpecific()
        self.setupStyle()
        self.setupMenus()
        self.setupProgress()
        self.setupErrorHandler()
        self.setupSignals()
        self.setupAutoUpdate()
        self.setupHooks()
        self.setupRefreshTimer()
        self.updateTitleBar()
        # screens
        self.setupDeckBrowser()
        self.setupOverview()
        self.setupReviewer()

    def setupProfileAfterWebviewsLoaded(self):
        for webWidget in (self.web, self.bottomWeb):
            if not webWidget._domDone:
                self.progress.timer(10, self.setupProfileAfterWebviewsLoaded, False, requiresCollection=False)
                return
            else:
                webWidget.requiresCol = True

        self.setupProfile()

    # Profiles
    ##########################################################################

    class ProfileManager(QMainWindow):
        onClose = pyqtSignal()
        closeFires = True

        def closeEvent(self, evt):
            if self.closeFires:
                self.onClose.emit()
            evt.accept()

        def closeWithoutQuitting(self):
            self.closeFires = False
            self.close()
            self.closeFires = True

    def setupProfile(self):
        if self.pm.meta['firstRun']:
            # load the new deck user profile
            self.pm.load(self.pm.profiles()[0])
            self.pm.meta['firstRun'] = False
            self.pm.save()

        self.pendingImport = None
        self.restoringBackup = False
        # profile not provided on command line?
        if not self.pm.name:
            # if there's a single profile, load it automatically
            profs = self.pm.profiles()
            if len(profs) == 1:
                self.pm.load(profs[0])
        if not self.pm.name:
            self.showProfileManager()
        else:
            self.loadProfile()

    def showProfileManager(self):
        self.pm.profile = None
        self.state = "profileManager"
        self.profileDiag = self.ProfileManager()
        profileForm = self.profileForm = aqt.forms.profiles.Ui_MainWindow()
        profileForm.setupUi(self.profileDiag)
        profileForm.login.clicked.connect(self.onOpenProfile)
        profileForm.profiles.itemDoubleClicked.connect(self.onOpenProfile)
        profileForm.openBackup.clicked.connect(self.onOpenBackup)
        profileForm.quit.clicked.connect(self.profileDiag.close)
        self.profileDiag.onClose.connect(self.cleanupAndExit)
        profileForm.add.clicked.connect(self.onAddProfile)
        profileForm.rename.clicked.connect(self.onRenameProfile)
        profileForm.delete_2.clicked.connect(self.onRemProfile)
        profileForm.profiles.currentRowChanged.connect(self.onProfileRowChange)
        profileForm.statusbar.setVisible(False)
        # enter key opens profile
        QShortcut(QKeySequence("Return"), self.profileDiag, activated=self.onOpenProfile)
        self.refreshProfilesList()
        # raise first, for osx testing
        self.profileDiag.show()
        self.profileDiag.activateWindow()
        self.profileDiag.raise_()

    def refreshProfilesList(self):
        profileForm = self.profileForm
        profileForm.profiles.clear()
        profs = self.pm.profiles()
        profileForm.profiles.addItems(profs)
        try:
            idx = profs.index(self.pm.name)
        except:
            idx = 0
        profileForm.profiles.setCurrentRow(idx)

    def onProfileRowChange(self, profileIndex):
        if profileIndex < 0:
            # called on .clear()
            return
        name = self.pm.profiles()[profileIndex]
        profileForm = self.profileForm
        self.pm.load(name)

    def openProfile(self):
        name = self.pm.profiles()[self.profileForm.profiles.currentRow()]
        return self.pm.load(name)

    def onOpenProfile(self):
        self.loadProfile(self.profileDiag.closeWithoutQuitting)

    def profileNameOk(self, str):
        return not checkInvalidFilename(str)

    def onAddProfile(self):
        name = getOnlyText(_("Name:")).strip()
        if name:
            if name in self.pm.profiles():
                return showWarning(_("Name exists."))
            if not self.profileNameOk(name):
                return
            self.pm.create(name)
            self.pm.name = name
            self.refreshProfilesList()

    def onRenameProfile(self):
        name = getOnlyText(_("New name:"), default=self.pm.name).strip()
        if not name:
            return
        if name == self.pm.name:
            return
        if name in self.pm.profiles():
            return showWarning(_("Name exists."))
        if not self.profileNameOk(name):
            return
        self.pm.rename(name)
        self.refreshProfilesList()

    def onRemProfile(self):
        profs = self.pm.profiles()
        if len(profs) < 2:
            return showWarning(_("There must be at least one profile."))
        # sure?
        if not askUser(_("""\
All cards, notes, and media for this profile will be deleted. \
Are you sure?"""), msgfunc=QMessageBox.warning, defaultno=True):
            return
        self.pm.remove(self.pm.name)
        self.refreshProfilesList()

    def onOpenBackup(self):
        if not askUser(_("""\
Replace your collection with an earlier backup?"""),
                       msgfunc=QMessageBox.warning,
                       defaultno=True):
            return
        def doOpen(path):
            self._openBackup(path)
        getFile(self.profileDiag, _("Revert to backup"),
                cb=doOpen, filter="*.colpkg", dir=self.pm.backupFolder())

    def _openBackup(self, path):
        try:
            # move the existing collection to the trash, as it may not open
            self.pm.trashCollection()
        except:
            showWarning(_("Unable to move existing file to trash - please try restarting your computer."))
            return

        self.pendingImport = path
        self.restoringBackup = True

        showInfo(_("""\
Automatic syncing and backups have been disabled while restoring. To enable them again, \
close the profile or restart Anki."""))

        self.onOpenProfile()

    def loadProfile(self, onsuccess=None):
        self.maybeAutoSync()

        if not self.loadCollection():
            return

        # show main window
        if self.pm.profile['mainWindowState']:
            restoreGeom(self, "mainWindow")
            restoreState(self, "mainWindow")
        # titlebar
        self.setWindowTitle(self.pm.name + " - Anki")
        # font size
        self.changeFontSize()
        # show and raise window for osx
        self.show()
        self.activateWindow()
        self.raise_()

        # import pending?
        if self.pendingImport:
            self.handleImport(self.pendingImport)
            self.pendingImport = None
        runHook("profileLoaded")
        if onsuccess:
            onsuccess()

    def unloadProfile(self, onsuccess):
        def callback():
            self._unloadProfile()
            onsuccess()

        runHook("unloadProfile")
        self.unloadCollection(callback)

    def _unloadProfile(self):
        self.pm.profile['mainWindowGeom'] = self.saveGeometry()
        self.pm.profile['mainWindowState'] = self.saveState()
        self.pm.save()
        self.hide()

        self.restoringBackup = False

        # at this point there should be no windows left
        self._checkForUnclosedWidgets()

        self.maybeAutoSync()

    def _checkForUnclosedWidgets(self):
        for topLevelWidget in self.app.topLevelWidgets():
            if topLevelWidget.isVisible():
                # windows with this property are safe to close immediately
                if getattr(topLevelWidget, "silentlyClose", None):
                    topLevelWidget.close()
                else:
                    print("Window should have been closed: {}".format(topLevelWidget))

    def unloadProfileAndExit(self):
        self.unloadProfile(self.cleanupAndExit)

    def unloadProfileAndShowProfileManager(self):
        self.unloadProfile(self.showProfileManager)

    def cleanupAndExit(self):
        self.errorHandler.unload()
        self.mediaServer.shutdown()
        anki.sound.cleanupMPV()
        self.app.exit(0)

    # Sound/video
    ##########################################################################

    def setupSound(self):
        if isWin:
            return
        try:
            anki.sound.setupMPV()
        except FileNotFoundError:
            print("mpv not found, reverting to mplayer")
        except anki.mpv.MPVProcessError:
            print("mpv too old, reverting to mplayer")

    # Collection load/unload
    ##########################################################################

    def loadCollection(self):
        try:
            return self._loadCollection()
        except Exception as e:
            t=_("""\
Anki was unable to open your collection file. If problems persist after \
restarting your computer, please use the Open Backup button in the profile \
manager.

Debug info:
""")+traceback.format_exc()
            showWarning(t)
            print(t, file = sys.stderr)
            # clean up open collection if possible
            if self.col:
                try:
                    self.col.close(save=False)
                except:
                    pass
                self.col = None

            # return to profile manager
            self.hide()
            self.showProfileManager()
            return False

    def _loadCollection(self):
        cpath = self.pm.collectionPath()

        def DeckManager(*args, **kwargs):
            return aqt.decks.DeckManager(self, *args, **kwargs)
        self.col = Collection(cpath, log=True, DeckManager=DeckManager)
        self.col.decks.mw = self

        self.setEnabled(True)
        self.progress.setupDB(self.col.db)
        self.maybeEnableUndo()
        self.moveToState("deckBrowser")
        return True

    def unloadCollection(self, onsuccess):
        def callback():
            self.setEnabled(False)
            self._unloadCollection()
            onsuccess()

        self.closeAllWindows(callback)

    def _unloadCollection(self):
        if not self.col:
            return
        if self.restoringBackup:
            label = _("Closing...")
        else:
            label = _("Backing Up...")
        self.progress.start(label=label, immediate=True)
        corrupt = False
        try:
            self.maybeOptimize()
            if not devMode:
                corrupt = self.col.db.scalar("pragma integrity_check") != "ok"
        except:
            corrupt = True
        try:
            self.col.close()
        except:
            corrupt = True
            print(traceback.format_exc(), file=sys.stderr)
        finally:
            self.col = None
        if corrupt:
            showWarning(_("Your collection file appears to be corrupt. \
This can happen when the file is copied or moved while Anki is open, or \
when the collection is stored on a network or cloud drive. If problems \
persist after restarting your computer, please open an automatic backup \
from the profile screen."))
        if not corrupt and not self.restoringBackup:
            self.backup()

        self.progress.finish()

    # Backup and auto-optimize
    ##########################################################################

    class BackupThread(Thread):
        def __init__(self, path, data):
            Thread.__init__(self)
            self.path = path
            self.data = data
            # create the file in calling thread to ensure the same
            # file is not created twice
            open(self.path, "wb").close()

        def run(self):
            zip = zipfile.ZipFile(self.path, "w", zipfile.ZIP_DEFLATED)
            zip.writestr("collection.anki2", self.data)
            zip.writestr("media", "{}")
            zip.close()

    def backup(self):
        if not self.pm.profile['numBackups'] or devMode:
            return
        currentTime = time.localtime(time.time())
        # Saving currentTime so that there is no change between successive
        # files if by acciddent a backup occurs before and after
        # midnightp
        self.year = int(time.strftime("%Y",currentTime))
        self.month = int(time.strftime("%m",currentTime))
        self.day = int(time.strftime("%d",currentTime))
        self.doBackup()
        self.cleanBackup()

    def doBackup(self):
        # do backup
        patternsToCreate = {"backup-%Y-%m-%d-%H.%M.%S.colpkg"}
        if self.pm.profile.get('longTermBackup', True):
            patternsToCreate.update({f"backup-yearly-{self.year:02d}.colpkg",
                                     f"backup-monthly-{self.year:02d}-{self.month:02d}.colpkg",
                                     f"backup-daily-{self.year:02d}-{self.month:02d}-{self.day:02d}.colpkg",})
        filesToCreate = {time.strftime(pattern, time.localtime(time.time()))
                         for pattern in patternsToCreate}
        with open(self.pm.collectionPath(), "rb") as file:
            data = file.read()
        for fname in filesToCreate:
            newpath = os.path.join(self.pm.backupFolder(), fname)
            if not os.path.exists(newpath):
                self.BackupThread(newpath, data).start()

    def cleanBackup(self):
        self.cleanRecentBackup()
        self.cleanLongTermBackup()

    def cleanRecentBackup(self):
        # find existing backups
        backups = [file
                   for file in os.listdir(self.pm.backupFolder())
                   # only look for new-style format
                   if not re.match(r"backup-\d{4}-\d{2}-.+.colpkg", file)]
        backups.sort()

        # remove old ones
        while len(backups) > self.pm.profile['numBackups']:
            fname = backups.pop(0)
            path = os.path.join(self.pm.backupFolder(), fname)
            os.unlink(path)

    def cleanLongTermBackup(self):
        nbDayInMonth = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
        monthsToKeep = []
        for nbMonth in range(12):
            if nbMonth<self.month:
                monthsToKeep.append((self.year, self.month-nbMonth))
            else:
                monthsToKeep.append((self.year-1, self.month-nbMonth+12))

        nbDayThisMonth = nbDayInMonth[self.month-1]
        nbDayPreviousMonth = nbDayInMonth[(self.month-2) % 12]

        daysToKeep = []
        for nbDay in range(nbDayThisMonth):
            if nbDay<self.day:
                daysToKeep.append((self.year, self.month, self.day-nbDay))
            else:
                if self.month == 1:
                    daysToKeep.append((self.year-1, 12, self.day-nbDay+nbDayPreviousMonth))
                else:
                    daysToKeep.append((self.year, self.month-1, self.day-nbDay+nbDayPreviousMonth))
        filesToKeep = ([f"backup-monthly-{yearToHave:02d}-{monthToHave:02d}.colpkg" for yearToHave, monthToHave in monthsToKeep]+
                       [f"backup-daily-{yearToHave:02d}-{monthToHave:02d}-{dayToHave:02d}.colpkg" for yearToHave, monthToHave, dayToHave in daysToKeep])
        for file in os.listdir(self.pm.backupFolder()):
            if (file.startswith("backup-monthy-") or file.startswith("backup-daily-")) and file not in filesToKeep:
                oldpath = os.path.join(self.pm.backupFolder(), file)
                os.unlink(oldpath)

    def maybeOptimize(self):
        # have two weeks passed?
        if (intTime() - self.pm.profile['lastOptimize']) < 86400*14:
            return
        self.progress.start(label=_("Optimizing..."), immediate=True)
        self.col.optimize()
        self.pm.profile['lastOptimize'] = intTime()
        self.pm.save()
        self.progress.finish()

    # State machine
    ##########################################################################

    def moveToState(self, state, *args):
        """Call self._oldStateCleanup(state) if it exists for oldState. It seems it's the
        case only for review.
        remove shortcut related to this state
        run hooks beforeStateChange and afterStateChange. By default they are empty.
        show the bottom, unless its reset required.
        """
        #print("-> move from", self.state, "to", state)
        oldState = self.state or "dummy"
        cleanup = getattr(self, "_"+oldState+"Cleanup", None)
        if cleanup:
            # pylint: disable=not-callable
            cleanup(state)
        self.clearStateShortcuts()
        self.state = state
        runHook('beforeStateChange', state, oldState, *args)
        getattr(self, "_"+state+"State")(oldState, *args)
        if state != "resetRequired":
            self.bottomWeb.show()
        runHook('afterStateChange', state, oldState, *args)

    def _deckBrowserState(self, oldState):
        self.deckBrowser.show()

    def _colLoadingState(self, oldState):
        "Run once, when col is loaded."
        self.enableColMenuItems()
        # ensure cwd is set if media dir exists
        self.col.media.dir()
        runHook("colLoading", self.col)
        self.moveToState("overview")

    def _selectedDeck(self):
        did = self.col.decks.selected()
        if not self.col.decks.nameOrNone(did):
            showInfo(_("Please select a deck."))
            return
        return self.col.decks.get(did)

    def _overviewState(self, oldState):
        if not self._selectedDeck():
            return self.moveToState("deckBrowser")
        self.col.reset()
        self.overview.show()

    def _reviewState(self, oldState):
        self.reviewer.show()

    def _reviewCleanup(self, newState):
        """Run hook "reviewCleanup". Unless new state is resetRequired or review."""
        if newState != "resetRequired" and newState != "review":
            self.reviewer.cleanup()

    def noteChanged(self, nid):
        "Called when a card or note is edited (but not deleted)."
        runHook("noteChanged", nid)

    # Resetting state
    ##########################################################################

    def reset(self, guiOnly=False):
        """Called for non-trivial edits. Rebuilds queue and updates UI.

        set Edit>undo
        change state (show the bottom bar, remove shortcut from last state)
        run hooks beforeStateChange and afterStateChange. By default they are empty.
        call cleanup of last state.
        call the hook "reset". It contains at least the onReset method
        from the current window if it is browser, (and its
        changeModel), editCurrent, addCard, studyDeck,
        modelChooser. Reset reinitialize those window without closing
        them.

        unless guiOnly:
        Deal with the fact that it's potentially a new day.
        Reset number of learning, review, new cards according to current decks
        empty queues. Set haveQueues to true.
        """
        if self.col:
            if not guiOnly:
                self.col.reset()
            runHook("reset")
            self.maybeEnableUndo()
            self.moveToState(self.state)

    def requireReset(self, modal=False):
        "Signal queue needs to be rebuilt when edits are finished or by user."
        self.autosave()
        self.resetModal = modal
        if self.interactiveState():
            self.moveToState("resetRequired")

    def interactiveState(self):
        "True if not in profile manager, syncing, etc."
        return self.state in ("overview", "review", "deckBrowser")

    def maybeReset(self):
        self.autosave()
        if self.state == "resetRequired":
            self.state = self.returnState
            self.reset()

    def delayedMaybeReset(self):
        # if we redraw the page in a button click event it will often crash on
        # windows
        self.progress.timer(100, self.maybeReset, False)

    def _resetRequiredState(self, oldState):
        if oldState != "resetRequired":
            self.returnState = oldState
        if self.resetModal:
            # we don't have to change the webview, as we have a covering window
            return
        self.web.resetHandlers()
        self.web.onBridgeCmd = lambda url: self.delayedMaybeReset()
        waitEditMessage = _("Waiting for editing to finish.")
        refreshButton = self.button("refresh", _("Resume Now"), id="resume")
        self.web.stdHtml("""
<center><div style="height: 100%%">
<div style="position:relative; vertical-align: middle;">
%s<br><br>
%s</div></div></center>
<script>$('#resume').focus()</script>
""" % (waitEditMessage, refreshButton))
        self.bottomWeb.hide()
        self.web.setFocus()

    # HTML helpers
    ##########################################################################

    def button(self, link, name, key=None, class_="", id="", extra=""):
        class_ = "but "+ class_
        if key:
            key = _("Shortcut key: %s") % key
        else:
            key = ""
        return '''
<button id="%s" class="%s" onclick="pycmd('%s');return false;"
title="%s" %s>%s</button>''' % (
            id, class_, link, key, extra, name)

    # Main window setup
    ##########################################################################

    def setupMainWindow(self):
        # main window
        self.form = aqt.forms.main.Ui_MainWindow()
        self.form.setupUi(self)
        # toolbar
        tweb = self.toolbarWeb = aqt.webview.AnkiWebView()
        tweb.title = "top toolbar"
        tweb.setFocusPolicy(Qt.WheelFocus)
        self.toolbar = aqt.toolbar.Toolbar(self, tweb)
        self.toolbar.draw()
        # main area
        self.web = aqt.webview.AnkiWebView()
        self.web.title = "main webview"
        self.web.setFocusPolicy(Qt.WheelFocus)
        self.web.setMinimumWidth(400)
        # bottom area
        sweb = self.bottomWeb = aqt.webview.AnkiWebView()
        sweb.title = "bottom toolbar"
        sweb.setFocusPolicy(Qt.WheelFocus)
        # add in a layout
        self.mainLayout = QVBoxLayout()
        self.mainLayout.setContentsMargins(0,0,0,0)
        self.mainLayout.setSpacing(0)
        self.mainLayout.addWidget(tweb)
        self.mainLayout.addWidget(self.web)
        self.mainLayout.addWidget(sweb)
        self.form.centralwidget.setLayout(self.mainLayout)

        # force webengine processes to load before cwd is changed
        if isWin:
            for webWidget in self.web, self.bottomWeb:
                webWidget.requiresCol = False
                webWidget._domReady = False
                webWidget._page.setContent(bytes("", "ascii"))

    def closeAllWindows(self, onsuccess):
        aqt.dialogs.closeAll(onsuccess)

    def executeInProgress(self, fun, immediate=True):
        self.progress.start(immediate=immediate)
        try:
            return fun()
        finally:
            self.progress.finish()

    # Components
    ##########################################################################

    def setupSignals(self):
        signal.signal(signal.SIGINT, self.onSigInt)

    def onSigInt(self, signum, frame):
        # interrupt any current transaction and schedule a rollback & quit
        if self.col:
            self.col.db.interrupt()
        def quit():
            self.col.db.rollback()
            self.close()
        self.progress.timer(100, quit, False)

    def setupProgress(self):
        self.progress = aqt.progress.ProgressManager(self)

    def setupErrorHandler(self):
        import aqt.errors
        self.errorHandler = aqt.errors.ErrorHandler(self)

    def setupAddons(self):
        import aqt.addons
        self.addonManager = aqt.addons.AddonManager(self)
        if not self.safeMode:
            self.addonManager.loadAddons()

    def setupSpellCheck(self):
        os.environ["QTWEBENGINE_DICTIONARIES_PATH"] = (
            os.path.join(self.pm.base, "dictionaries"))

    def setupThreads(self):
        self._mainThread = QThread.currentThread()

    def inMainThread(self):
        return self._mainThread == QThread.currentThread()

    def setupDeckBrowser(self):
        from aqt.deckbrowser import DeckBrowser
        self.deckBrowser = DeckBrowser(self)

    def setupOverview(self):
        from aqt.overview import Overview
        self.overview = Overview(self)

    def setupReviewer(self):
        from aqt.reviewer import Reviewer
        self.reviewer = Reviewer(self)

    # Syncing
    ##########################################################################

    # expects a current profile and a loaded collection; reloads
    # collection after sync completes
    def onSync(self):
        self.unloadCollection(self._onSync)

    def _onSync(self):
        self._sync()
        if not self.loadCollection():
            return

    # expects a current profile, but no collection loaded
    def maybeAutoSync(self):
        if (not self.pm.profile['syncKey']
            or not self.pm.profile['autoSync']
            or self.safeMode
            or self.restoringBackup):
            return

        # ok to sync
        self._sync()

    def _sync(self):
        from aqt.sync import SyncManager
        self.state = "sync"
        self.syncer = SyncManager(self, self.pm)
        self.syncer.sync()
        if self.pm.profile.get("syncAddons", True):
            self.addonManager.onCheckForUpdates(False)

    # Tools
    ##########################################################################

    def raiseMain(self):
        if not self.app.activeWindow():
            # make sure window is shown
            self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        return True

    def setupStyle(self):
        buf = ""

        if isWin and platform.release() == '10':
            # add missing bottom border to menubar
            buf += """
QMenuBar {
  border-bottom: 1px solid #aaa;
  background: white;
}
"""
            # qt bug? setting the above changes the browser sidebar
            # to white as well, so set it back
            buf += """
QTreeWidget {
  background: #eee;
}
            """

        # allow addons to modify the styling
        buf = runFilter("setupStyle", buf)

        # allow users to extend styling
        stylePath = os.path.join(aqt.mw.pm.base, "style.css")
        if os.path.exists(stylePath):
            buf += open(stylePath).read()

        self.app.setStyleSheet(buf)

    # Key handling
    ##########################################################################

    def setupKeys(self):
        globalShortcuts = [
            ("Ctrl+:", self.onDebug),
            ("d", lambda: self.moveToState("deckBrowser")),
            ("s", self.onStudyKey),
            ("a", self.onAddCard),
            ("b", self.onBrowse),
            ("t", self.onStats),
            ("y", self.onSync)
        ]
        self.applyShortcuts(globalShortcuts)

        self.stateShortcuts = []

    def applyShortcuts(self, shortcuts):
        """A list of shortcuts.

        Keyword arguments:
        shortcuts -- a list of pair (shortcut key, function called by the shortcut)
        """
        qshortcuts = [QShortcut(QKeySequence(key), self, activated=fn)
                      for key, fn in shortcuts]
        for scut in qshortcuts:
            scut.setAutoRepeat(False)
        return qshortcuts

    def setStateShortcuts(self, shortcuts):
        """set stateShortcuts to QShortcut from shortcuts

        run hook CURRENTSTATEStateShorcuts
        """
        runHook(self.state+"StateShortcuts", shortcuts)
        self.stateShortcuts = self.applyShortcuts(shortcuts)

    def clearStateShortcuts(self):
        """Delete the shortcut of current state, empty stateShortcuts"""
        for qs in self.stateShortcuts:
            sip.delete(qs)
        self.stateShortcuts = []

    def onStudyKey(self):
        if self.state == "overview":
            self.col.startTimebox()
            self.moveToState("review")
        else:
            self.onReviewOrOverview()

    # App exit
    ##########################################################################

    def closeEvent(self, event):
        if self.state == "profileManager":
            # if profile manager active, this event may fire via OS X menu bar's
            # quit option
            self.profileDiag.close()
            event.accept()
        else:
            # ignore the event for now, as we need time to clean up
            event.ignore()
            self.unloadProfileAndExit()

    # Undo & autosave
    ##########################################################################

    def onUndo(self):
        name = self.col.undoName()
        if not name:
            return
        cid = self.col.undo()
        if cid and self.state == "review":
            card = self.col.getCard(cid)
            self.col.sched.reset()
            self.reviewer.cardQueue.append(card)
            self.reviewer.nextCard()
            runHook("revertedCard", cid)
        else:
            self.reset()
            tooltip(_("Reverted to state prior to '%s'.") % name.lower())
            runHook("revertedState", name)
        self.maybeEnableUndo()

    def maybeEnableUndo(self):
        """Enable undo in the GUI if something can be undone. Call the hook undoState(somethingCanBeUndone)."""
        if self.col and self.col.undoName():#Whether something can be undone
            self.form.actionUndo.setText(_("Undo %s") %
                                            self.col.undoName())
            self.form.actionUndo.setEnabled(True)
            runHook("undoState", True)
        else:
            self.form.actionUndo.setText(_("Undo"))
            self.form.actionUndo.setEnabled(False)
            runHook("undoState", False)

    def checkpoint(self, name):
        self.col.save(name)
        self.maybeEnableUndo()

    def autosave(self):
        saved = self.col.autosave()
        self.maybeEnableUndo()
        if saved:
            self.doGC()

    # Other menu operations
    ##########################################################################

    def onAddCard(self):
        """Open the addCards window."""
        return aqt.dialogs.open("AddCards", self)

    def onBrowse(self):
        """Open the browser window."""
        choice = self.col.conf.get("browserFromReviewer", "default")
        if self.state == "review" and self.reviewer.card is not None:
            if choice == "cid":
                return aqt.dialogs.open("Browser", self, f"cid:{self.reviewer.card.id}")
            elif choice == "nid":
                return aqt.dialogs.open("Browser", self, f"nid:{self.reviewer.card.nid}")
            elif choice == "nid":
                return aqt.dialogs.open("Browser", self, f"did:{self.reviewer.card.did}")
        return aqt.dialogs.open("Browser", self)

    def onEditCurrent(self):
        """Open the editing window."""
        return aqt.dialogs.open("EditCurrent", self)

    def onDeckConf(self, deck=None):
        """Open the deck editor.

        According to whether the deck is dynamic or not, open distinct window
        keyword arguments:
        deck -- The deck to edit. If not give, current Deck"""
        if not deck:
            deck = self.col.decks.current()
        if deck.isDyn():
            import aqt.dyndeckconf
            return aqt.dyndeckconf.DeckConf(self, deck=deck)
        else:
            import aqt.deckconf
            return aqt.deckconf.DeckConf(self, deck)

    def onOverview(self):
        self.col.reset()
        self.moveToState("overview")

    def onReviewOrOverview(self):
        if self.col.conf.get("deckOverview", False):
            self.onOverview()
        else:
            self.onReview()

    def onReview(self):
        self.col.startTimebox()
        self.moveToState("review")

    def onStats(self):
        """Open stats for selected decks

        If there are no selected deck, don't do anything."""
        deck = self._selectedDeck()
        if not deck:
            return
        return aqt.dialogs.open("DeckStats", self)

    def onPrefs(self):
        """Open preference window"""
        return aqt.dialogs.open("Preferences", self)

    def onNoteTypes(self):
        import aqt.models
        return aqt.models.Models(self, self, fromMain=True)

    def onAbout(self):
        """Open the about window"""
        return aqt.dialogs.open("About", self)

    def onPostpone_Reviews(self):
        self.addDelay(self.col.getReviewCards())

    def onDonate(self):
        """Ask the OS to open the donate web page"""
        return openLink(aqt.appDonate)

    def onDocumentation(self):
        """Ask the OS to open the documentation web page"""
        openHelp("")

    def addDelay(self, cids):
        (delay, delayResp) = getText("How many day to add to cards ? (negative number to substract days)")
        try:
            delay = int(delay)
        except ValueError:
            showWarning("Please enter an integral number of days")
            return None
        if (not delayResp) or delay == 0:
            return None
        self.checkpoint("Adding delay")
        self.progress.start()
        self.col.addDelay(cids, delay)
        self.progress.finish()
        self.col.reset()
        self.reset()
        tooltip(_("""Delay added."""))



    # Importing & exporting
    ##########################################################################

    def handleImport(self, path):
        import aqt.importing
        if not os.path.exists(path):
            return showInfo(_("Please use File>Import to import this file."))

        aqt.importing.importFile(self, path)

    def onImport(self):
        import aqt.importing
        aqt.importing.onImport(self)

    def onExport(self, deck=None):
        """Open exporting window, with deck as in its argument."""
        import aqt.exporting
        aqt.exporting.ExportDialog(self, deck=deck)

    # Cramming
    ##########################################################################

    def onCram(self, search=""):
        import aqt.dyndeckconf
        index = 1
        deck = self.col.decks.current()
        if not search:
            if not deck.isDyn():
                search = 'deck:"%s" ' % deck.getName()
        decks = self.col.decks.allNames()
        while _("Filtered Deck %d") % index in decks:
            index += 1
        name = _("Filtered Deck %d") % index
        deck = self.col.decks.newDyn(name)
        diag = aqt.dyndeckconf.DeckConf(self, first=True, search=search)
        if not diag.ok:
            # user cancelled first config
            deck.rem()
            deck.select()

    # Menu, title bar & status
    ##########################################################################

    def setupMenus(self):
        menu = self.form
        menu.actionSwitchProfile.triggered.connect(
            self.unloadProfileAndShowProfileManager)
        menu.actionImport.triggered.connect(self.onImport)
        menu.actionExport.triggered.connect(self.onExport)
        menu.actionExit.triggered.connect(self.close)
        menu.actionPreferences.triggered.connect(self.onPrefs)
        menu.actionPostpone_Reviews.triggered.connect(self.onPostpone_Reviews)
        menu.actionAbout.triggered.connect(self.onAbout)
        menu.actionUndo.triggered.connect(self.onUndo)
        if qtminor < 11:
            menu.actionUndo.setShortcut(QKeySequence(_("Ctrl+Alt+Z")))
        menu.actionFullDatabaseCheck.triggered.connect(self.onCheckDB)
        menu.actionCheckMediaDatabase.triggered.connect(self.onCheckMediaDB)
        menu.actionDocumentation.triggered.connect(self.onDocumentation)
        menu.actionDonate.triggered.connect(self.onDonate)
        menu.actionStudyDeck.triggered.connect(self.onStudyDeck)
        menu.actionCreateFiltered.triggered.connect(self.onCram)
        menu.actionEmptyCards.triggered.connect(self.onEmptyCards)
        menu.actionNoteTypes.triggered.connect(self.onNoteTypes)

    def updateTitleBar(self):
        self.setWindowTitle("Anki")

    # Auto update
    ##########################################################################

    def setupAutoUpdate(self):
        import aqt.update
        self.autoUpdate = aqt.update.LatestVersionFinder(self)
        self.autoUpdate.newVerAvail.connect(self.newVerAvail)
        self.autoUpdate.newMsg.connect(self.newMsg)
        self.autoUpdate.clockIsOff.connect(self.clockIsOff)
        self.autoUpdate.start()

    def newVerAvail(self, ver):
        if self.pm.meta.get('suppressUpdate', None) != ver:
            aqt.update.askAndUpdate(self, ver)

    def newMsg(self, data):
        aqt.update.showMessages(self, data)

    def clockIsOff(self, diff):
        diffText = ngettext("%s second", "%s seconds", diff) % diff
        warn = _("""\
In order to ensure your collection works correctly when moved between \
devices, Anki requires your computer's internal clock to be set correctly. \
The internal clock can be wrong even if your system is showing the correct \
local time.

Please go to the time settings on your computer and check the following:

- AM/PM
- Clock drift
- Day, month and year
- Timezone
- Daylight savings

Difference to correct time: %s.""") % diffText
        showWarning(warn)
        self.app.closeAllWindows()

    # Count refreshing
    ##########################################################################

    def setupRefreshTimer(self):
        # every 10 minutes
        self.progress.timer(10*60*1000, self.onRefreshTimer, True)

    def onRefreshTimer(self):
        if self.state == "deckBrowser":
            self.deckBrowser.refresh()
        elif self.state == "overview":
            self.overview.refresh()

    # Permanent libanki hooks
    ##########################################################################

    def setupHooks(self):
        """Adds onSchemadMod, onRemNotes and onOdueInvalid to their hooks"""
        addHook("modSchema", self.onSchemaMod)
        addHook("remNotes", self.onRemNotes)
        addHook("odueInvalid", self.onOdueInvalid)

        addHook("mpvWillPlay", self.onMpvWillPlay)
        addHook("mpvIdleHook", self.onMpvIdle)
        self._activeWindowOnPlay = None

    def onOdueInvalid(self):
        showWarning(_("""\
Invalid property found on card. Please use Tools>Check Database, \
and if the problem comes up again, please ask on the support site."""))

    def _isVideo(self, file):
        head, ext = os.path.splitext(file.lower())
        return ext in (".mp4", ".mov", ".mpg", ".mpeg", ".mkv", ".avi")

    def onMpvWillPlay(self, file):
        if not self._isVideo(file):
            return

        self._activeWindowOnPlay = self.app.activeWindow() or self._activeWindowOnPlay

    def onMpvIdle(self):
        if not self.app.activeWindow() and self._activeWindowOnPlay and not sip.isdeleted(self._activeWindowOnPlay) and self._activeWindowOnPlay.isVisible():
            self._activeWindowOnPlay.activateWindow()
            self._activeWindowOnPlay.raise_()
        self._activeWindowOnPlay = None

    # Log note deletion
    ##########################################################################

    def onRemNotes(self, col, nids):
        """Append (id, model id and fields) to the end of deleted.txt

        This is done for each id of nids.
        This method is added to the hook remNotes; and executed on note deletion.
        """
        path = os.path.join(self.pm.profileFolder(), "deleted.txt")
        existed = os.path.exists(path)
        with open(path, "ab") as file:
            if not existed:
                file.write(b"nid\tmid\tfields\n")
            for id, mid, flds in col.db.execute(
                    "select id, mid, flds from notes where id in %s" %
                ids2str(nids)):
                fields = splitFields(flds)
                file.write(("\t".join([str(id), str(mid)] + fields)).encode("utf8"))
                file.write(b"\n")

    # Schema modifications
    ##########################################################################

    def onSchemaMod(self, arg):
        """Ask the user whether they accept to do an action which will request a full reupload of the db"""
        return askUser(_("""\
The requested change will require a full upload of the database when \
you next synchronize your collection. If you have reviews or other changes \
waiting on another device that haven't been synchronized here yet, they \
will be lost. Continue?"""))

    # Advanced features
    ##########################################################################

    def onCheckDB(self):
        "True if no problems"
        self.progress.start(immediate=True)
        ret, ok = self.col.fixIntegrity()
        self.progress.finish()
        if not ok:
            showText(ret)
        else:
            tooltip(ret)

        # if an error has directed the user to check the database,
        # silently clean up any broken reset hooks which distract from
        # the underlying issue
        while True:
            try:
                self.reset()
                break
            except Exception as e:
                print("swallowed exception in reset hook:", e)
                continue
        return ret

    def onCheckMediaDB(self):
        self.progress.start(immediate=True)
        (nohave, unused, warnings) = self.col.media.check()
        if nohave and self.col.conf.get("browserOnMissingMedia", True): # open browser with missing medias
            browser = aqt.dialogs.open("Browser", self)
            browser.form.searchEdit.lineEdit().setText("tag:MissingMedia")
            browser.onSearchActivated()
        self.progress.finish()
        # generate report
        report = ""
        if warnings:
            report += "\n".join(warnings) + "\n"
        if unused:
            numberOfUnusedFilesLabel = len(unused)
            if report:
                report += "\n\n\n"
            report += ngettext("%d file found in media folder not used by any cards:",
                "%d files found in media folder not used by any cards:",
                numberOfUnusedFilesLabel) % numberOfUnusedFilesLabel
            report += "\n" + "\n".join(unused)
        if nohave:
            if report:
                report += "\n\n\n"
            report += _(
                "Used on cards but missing from media folder:")
            report += "\n" + "\n".join(nohave)
        if not report:
            tooltip(_("No unused or missing files found."))
            return
        # show report and offer to delete
        diag = QDialog(self)
        diag.setWindowTitle("Anki")
        layout = QVBoxLayout(diag)
        diag.setLayout(layout)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(report)
        layout.addWidget(text)
        box = QDialogButtonBox(QDialogButtonBox.Close)
        layout.addWidget(box)
        if unused:
            deleteButton = QPushButton(_("Delete Unused Files"))
            deleteButton.setAutoDefault(False)
            box.addButton(deleteButton, QDialogButtonBox.ActionRole)
            deleteButton.clicked.connect(
                lambda click, unused=unused, diag=diag: self.deleteUnused(unused, diag))

        box.rejected.connect(diag.reject)
        diag.setMinimumHeight(400)
        diag.setMinimumWidth(500)
        restoreGeom(diag, "checkmediadb")
        diag.exec_()
        saveGeom(diag, "checkmediadb")

    def deleteUnused(self, unused, diag):
        if not askUser(
            _("Delete unused media?")):
            return
        mdir = self.col.media.dir()
        self.progress.start(immediate=True)
        try:
            lastProgress = 0
            for index, file in enumerate(unused):
                path = os.path.join(mdir, file)
                if os.path.exists(path):
                    send2trash(path)

                now = time.time()
                if now - lastProgress >= 0.3:
                    numberOfRemainingFilesToBeDeleted = len(unused) - index
                    lastProgress = now
                    label = ngettext("%d file remaining...",
                    "%d files remaining...",
                    numberOfRemainingFilesToBeDeleted) % numberOfRemainingFilesToBeDeleted
                    self.progress.update(label)
        finally:
            self.progress.finish()
        # caller must not pass in empty list
        # pylint: disable=undefined-loop-variable
        numberOfFilesDeleted = index + 1
        tooltip(ngettext("Deleted %d file.",
        "Deleted %d files.",
        numberOfFilesDeleted) % numberOfFilesDeleted)
        diag.close()

    def onStudyDeck(self):
        from aqt.studydeck import StudyDeck
        ret = StudyDeck(
            self, dyn=True, current=self.col.decks.current().getName())
        if ret.name:
            self.col.decks.byName(ret.name, create=True).select()
            self.onReviewOrOverview()

    def onEmptyCards(self):
        """Method called by Tools>Empty Cards..."""

        self.progress.start(immediate=True)
        cids = set(self.col.emptyCids())
        if not cids:
            self.progress.finish()
            tooltip(_("No empty cards."))
            return
        report = self.col.emptyCardReport(cids)
        self.progress.finish()
        part1 = ngettext("%d card", "%d cards", len(cids)) % len(cids)
        part1 = _("%s to delete:") % part1
        diag, box = showText(part1 + "\n\n" + report, run=False,
                geomKey="emptyCards")
        box.addButton(_("Delete Cards"), QDialogButtonBox.AcceptRole)
        box.button(QDialogButtonBox.Close).setDefault(True)
        box.accepted.connect(lambda: this.onDelete(cids, diag))
        diag.show()

    def _delete(self, cids):
        self.col.remCards(cids)
        tooltip(ngettext("%d card deleted.", "%d cards deleted.", len(cids)) % len(cids))
        self.reset()

    def onDelete(self, cids, diag):
        """
        Delete cards with ids in cids. Close diag.

        If a note has no more card, either delete it or warn, depending on preferences.
        """
        saveGeom(diag, "emptyCards")
        QDialog.accept(diag)
        self.checkpoint(_("Delete Empty"))
        if not self.col.conf.get("keepEmptyNote", True):
            self._delete(cids)

        # Create a dic associating to each nid the cids to delete.
        nidToCidsToDelete = dict()
        for cid in cids:
            card = self.col.getCard(cid)
            note = card.note()
            nid = note.id
            if nid not in nidToCidsToDelete:
                nidToCidsToDelete[nid] = set()
            nidToCidsToDelete[nid].add(cid)

        # Compute the set of empty notes. Keep their cards
        emptyNids = set()
        for nid, cidsToDeleteOfNote in nidToCidsToDelete.items():
            note = self.col.getNote(nid)
            cidsOfNids = set([card.id for card in note.cards()])
            if cidsOfNids == cidsToDeleteOfNote:
                emptyNids.add(note.id)
                cids -= cidsOfNids

        # Deal with tags
        nidsWithTag = set(self.col.findNotes("tag:NoteWithNoCard"))
        for nid in emptyNids - nidsWithTag:
            note = self.col.getNote(nid)
            note.addTag("NoteWithNoCard")
            note.flush()
        for nid in nidsWithTag - emptyNids:
            note = self.col.getNote(nid)
            note.delTag("NoteWithNoCard")
            note.flush()

        # Warn about notes without cards.
        if emptyNids:
            showWarning(f"""{len(emptyNids)} note(s) should have been deleted because they had no more cards. They now have the tag "NoteWithNoCard". Please go check them. Then either edit them to save their content, or delete them from the browser.""")
            browser = aqt.dialogs.open("Browser", self)
            browser.form.searchEdit.lineEdit().setText("tag:NoteWithNoCard")
            browser.onSearchActivated()

        self._delete(cids)

    # Debugging
    ######################################################################

    def onDebug(self):
        self.debugDiag = QDialog()
        self.debugDiag.silentlyClose = True
        frm = aqt.forms.debug.Ui_Dialog()
        frm.setupUi(self.debugDiag)
        font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        font.setPointSize(frm.text.font().pointSize() + 1)
        frm.text.setFont(font)
        frm.log.setFont(font)
        self.debugDiagShort = QShortcut(QKeySequence("ctrl+return"), self.debugDiag)
        self.debugDiagShort.activated.connect(lambda: self.onDebugRet(frm))
        self.debugDiagShort = QShortcut(
            QKeySequence("ctrl+shift+return"), self.debugDiag)
        self.debugDiagShort.activated.connect(lambda: self.onDebugPrint(frm))
        self.debugDiagShort = QShortcut(QKeySequence("ctrl+l"), self.debugDiag)
        self.debugDiagShort.activated.connect(frm.log.clear)
        self.debugDiagShort = QShortcut(QKeySequence("ctrl+shift+l"), self.debugDiag)
        self.debugDiagShort.activated.connect(frm.text.clear)
        self.debugDiag.show()

    def _captureOutput(self, on):
        mw = self
        class Stream:
            def write(self, data):
                mw._output += data
        if on:
            self._output = ""
            self._oldStderr = sys.stderr
            self._oldStdout = sys.stdout
            stream = Stream()
            sys.stderr = stream
            sys.stdout = stream
        else:
            sys.stderr = self._oldStderr
            sys.stdout = self._oldStdout

    def _debugCard(self):
        return self.reviewer.card.__dict__

    def _debugBrowserCard(self):
        return aqt.dialogs._dialogs['Browser'][1].card.__dict__

    def onDebugPrint(self, frm):
        cursor = frm.text.textCursor()
        position = cursor.position()
        cursor.select(QTextCursor.LineUnderCursor)
        line = cursor.selectedText()
        pfx, sfx = "pp(", ")"
        if not line.startswith(pfx):
            line = "{}{}{}".format(pfx, line, sfx)
            cursor.insertText(line)
            cursor.setPosition(position + len(pfx))
            frm.text.setTextCursor(cursor)
        self.onDebugRet(frm)

    def onDebugRet(self, frm):
        import pprint, traceback
        text = frm.text.toPlainText()
        card = self._debugCard
        bcard = self._debugBrowserCard
        mw = self
        pp = pprint.pprint
        self._captureOutput(True)
        try:
            # pylint: disable=exec-used
            exec(text)
        except:
            self._output += traceback.format_exc()
        self._captureOutput(False)
        buf = ""
        for index, line in enumerate(text.strip().split("\n")):
            if index == 0:
                buf += ">>> %s\n" % line
            else:
                buf += "... %s\n" % line
        try:
            frm.log.appendPlainText(buf + (self._output or "<no output>"))
        except UnicodeDecodeError:
            frm.log.appendPlainText(_("<non-unicode text>"))
        frm.log.ensureCursorVisible()

    # System specific code
    ##########################################################################

    def setupSystemSpecific(self):
        self.hideMenuAccels = False
        if isMac:
            # mac users expect a minimize option
            self.minimizeShortcut = QShortcut("Ctrl+M", self)
            self.minimizeShortcut.activated.connect(self.onMacMinimize)
            self.hideMenuAccels = True
            self.maybeHideAccelerators()
            self.hideStatusTips()
        elif isWin:
            # make sure ctypes is bundled
            from ctypes import windll, wintypes
            _dummy = windll
            _dummy = wintypes

    def maybeHideAccelerators(self, tgt=None):
        if not self.hideMenuAccels:
            return
        tgt = tgt or self
        for action in tgt.findChildren(QAction):
            txt = str(action.text())
            match = re.match(r"^(.+)\(&.+\)(.+)?", txt)
            if match:
                action.setText(match.group(1) + (match.group(2) or ""))

    def hideStatusTips(self):
        for action in self.findChildren(QAction):
            action.setStatusTip("")

    def onMacMinimize(self):
        self.setWindowState(self.windowState() | Qt.WindowMinimized)

    # Single instance support
    ##########################################################################

    def setupAppMsg(self):
        self.app.appMsg.connect(self.onAppMsg)

    def onAppMsg(self, buf):
        if self.state == "startup":
            # try again in a second
            return self.progress.timer(1000, lambda: self.onAppMsg(buf), False,
                                       requiresCollection=False)
        elif self.state == "profileManager":
            # can't raise window while in profile manager
            if buf == "raise":
                return
            self.pendingImport = buf
            return tooltip(_("Deck will be imported when a profile is opened."))
        if not self.interactiveState() or self.progress.busy():
            # we can't raise the main window while in profile dialog, syncing, etc
            if buf != "raise":
                showInfo(_("""\
Please ensure a profile is open and Anki is not busy, then try again."""),
                     parent=None)
            return
        # raise window
        if isWin:
            # on windows we can raise the window by minimizing and restoring
            self.showMinimized()
            self.setWindowState(Qt.WindowActive)
            self.showNormal()
        else:
            # on osx we can raise the window. on unity the icon in the tray will just flash.
            self.activateWindow()
            self.raise_()
        if buf == "raise":
            return
        # import
        self.handleImport(buf)

    # GC
    ##########################################################################
    # ensure gc runs in main thread

    def setupDialogGC(self, obj):
        obj.finished.connect(lambda: self.gcWindow(obj))

    def gcWindow(self, obj):
        obj.deleteLater()
        self.progress.timer(1000, self.doGC, False, requiresCollection=False)

    def disableGC(self):
        gc.collect()
        gc.disable()

    def doGC(self):
        assert not self.progress.inDB
        gc.collect()

    # Crash log
    ##########################################################################

    def setupCrashLog(self):
        crashPath = os.path.join(self.pm.base, "crash.log")
        self._crashLog = open(crashPath, "ab", 0)
        faulthandler.enable(self._crashLog)

    # Media server
    ##########################################################################

    def setupMediaServer(self):
        self.mediaServer = aqt.mediasrv.MediaServer(self)
        self.mediaServer.start()

    def baseHTML(self):
        return '<base href="%s">' % self.serverURL()

    def serverURL(self):
        return "http://127.0.0.1:%d/" % self.mediaServer.getPort()

    def changeGlobalFontSize(self, font_size):
        font = QApplication.font()
        font.setPixelSize(font_size)
        QApplication.setFont(font)

    def changeWebFontSize(self, font_size):
        QWebEngineSettings.globalSettings().setFontSize(QWebEngineSettings.MinimumFontSize, font_size)

    def changeFontSize(self, font_size=None):
        if font_size is None:
            font_size = self.col.conf.get("fontSize", 12)
        self.changeGlobalFontSize(font_size)
        self.changeWebFontSize(font_size)
