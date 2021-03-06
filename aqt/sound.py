# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

import time

from anki.lang import _
from anki.sound import Recorder
from aqt.qt import *
from aqt.utils import restoreGeom, saveGeom, showWarning

if not Recorder:
    print("pyaudio not installed")

def getAudio(parent, encode=True):
    "Record and return filename"
    # record first
    if not Recorder:
        showWarning("pyaudio not installed")
        return

    recorder = Recorder()
    mb = QMessageBox(parent)
    restoreGeom(mb, "audioRecorder")
    mb.setWindowTitle("Anki")
    mb.setIconPixmap(QPixmap(":/icons/media-record.png"))
    but = QPushButton(_("Save"))
    mb.addButton(but, QMessageBox.AcceptRole)
    but.setDefault(True)
    but = QPushButton(_("Cancel"))
    mb.addButton(but, QMessageBox.RejectRole)
    mb.setEscapeButton(but)
    startTime = time.time()
    recorder.start()
    time.sleep(recorder.startupDelay)
    QApplication.instance().processEvents()
    while not mb.clickedButton():
        txt =_("Recording...<br>Time: %0.1f")
        mb.setText(txt % (time.time() - startTime))
        mb.show()
        QApplication.instance().processEvents()
    if mb.clickedButton() == mb.escapeButton():
        recorder.stop()
        recorder.cleanup()
        return
    saveGeom(mb, "audioRecorder")
    # ensure at least a second captured
    while time.time() - startTime < 1:
        time.sleep(0.1)
    recorder.stop()
    # process
    recorder.postprocess(encode)
    return recorder.file()
