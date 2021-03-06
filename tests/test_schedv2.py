# coding: utf-8

import copy
import time

from anki.consts import *
from anki.hooks import addHook
from anki.utils import intTime
from tests.shared import getEmptyCol


def test_clock():
    d = getEmptyCol()
    if (d.sched.dayCutoff - intTime()) < 10*60:
        raise Exception("Unit tests will fail around the day rollover.")

def checkRevIvl(d, c, targetIvl):
    min, max = d.sched._fuzzIvlRange(targetIvl)
    return min <= c.ivl <= max

def test_basics():
    d = getEmptyCol()
    d.reset()
    assert not d.sched.getCard()

def test_new():
    d = getEmptyCol()
    d.reset()
    assert d.sched.newCount() == 0
    # add a note
    f = d.newNote()
    f['Front'] = "one"; f['Back'] = "two"
    d.addNote(f)
    d.reset()
    assert d.sched.newCount() == 1
    # fetch it
    c = d.sched.getCard()
    assert c
    assert c.queue == QUEUE_NEW
    assert c.type == 0
    # if we answer it, it should become a learn card
    t = intTime()
    d.sched.answerCard(c, 1)
    assert c.queue == QUEUE_LRN
    assert c.type == CARD_LRN
    assert c.due >= t

    # disabled for now, as the learn fudging makes this randomly fail
    # # the default order should ensure siblings are not seen together, and
    # # should show all cards
    # m = d.models.current(); mm = d.models
    # t = m.newTemplate("Reverse",
    # "{{Back}}",
    #= "{{Front}}")
    # m.save()
    # f = d.newNote()
    # f['Front'] = u"2"; f['Back'] = u"2"
    # d.addNote(f)
    # f = d.newNote()
    # f['Front'] = u"3"; f['Back'] = u"3"
    # d.addNote(f)
    # d.reset()
    # qs = ("2", "3", "2", "3")
    # for n in range(4):
    #     c = d.sched.getCard()
    #     assert qs[n] in c.q()
    #     d.sched.answerCard(c, 2)

def test_newLimits():
    d = getEmptyCol()
    # add some notes
    g2 = d.decks.byName("Default::foo", create=True)
    for i in range(30):
        f = d.newNote()
        f['Front'] = str(i)
        if i > 4:
            f.model()['did'] = g2.getId()
        d.addNote(f)
    # give the child deck a different configuration
    c2 = d.decks.newConf("new conf")
    g2.setConf(c2)
    d.reset()
    # both confs have defaulted to a limit of 20
    assert d.sched.newCount() == 20
    # first card we get comes from parent
    c = d.sched.getCard()
    assert c.did == 1
    # limit the parent to 10 cards, meaning we get 10 in total
    conf1 = d.decks.get(1).getConf()
    conf1['new']['perDay'] = 10
    d.reset()
    assert d.sched.newCount() == 10
    # if we limit child to 4, we should get 9
    conf2 = g2.getConf()
    conf2['new']['perDay'] = 4
    d.reset()
    assert d.sched.newCount() == 9

def test_newBoxes():
    d = getEmptyCol()
    f = d.newNote()
    f['Front'] = "one"
    d.addNote(f)
    d.reset()
    c = d.sched.getCard()
    c.currentConf()['new']['delays'] = [1,2,3,4,5]
    d.sched.answerCard(c, 2)
    # should handle gracefully
    c.currentConf()['new']['delays'] = [1]
    d.sched.answerCard(c, 2)

def test_learn():
    d = getEmptyCol()
    # add a note
    f = d.newNote()
    f['Front'] = "one"; f['Back'] = "two"
    f = d.addNote(f)
    # set as a learn card and rebuild queues
    d.db.execute("update cards set queue=0, type=0")
    d.reset()
    # sched.getCard should return it, since it's due in the past
    c = d.sched.getCard()
    assert c
    c.currentConf()['new']['delays'] = [0.5, 3, 10]
    # fail it
    d.sched.answerCard(c, 1)
    # it should have three reps left to graduation
    assert c.left%1000 == 3
    assert c.left//1000 == 3
    # it should by due in 30 seconds
    t = round(c.due - time.time())
    assert t >= 25 and t <= 40
    # pass it once
    d.sched.answerCard(c, 3)
    # it should by due in 3 minutes
    dueIn = c.due - time.time()
    assert 179 <= dueIn <= 180*1.25
    assert c.left%1000 == 2
    assert c.left//1000 == 2
    # check log is accurate
    log = d.db.first("select * from revlog order by id desc")
    assert log[3] == 3
    assert log[4] == -180
    assert log[5] == -30
    # pass again
    d.sched.answerCard(c, 3)
    # it should by due in 10 minutes
    dueIn = c.due - time.time()
    assert 599 <= dueIn <= 600*1.25
    assert c.left%1000 == 1
    assert c.left//1000 == 1
    # the next pass should graduate the card
    assert c.queue == QUEUE_LRN
    assert c.type == CARD_LRN
    d.sched.answerCard(c, 3)
    assert c.queue == QUEUE_REV
    assert c.type == CARD_DUE
    # should be due tomorrow, with an interval of 1
    assert c.due == d.sched.today+1
    assert c.ivl == 1
    # or normal removal
    c.type = 0
    c.queue = QUEUE_LRN
    d.sched.answerCard(c, 4)
    assert c.type == CARD_DUE
    assert c.queue == QUEUE_REV
    assert checkRevIvl(d, c, 4)
    # revlog should have been updated each time
    assert d.db.scalar("select count() from revlog where type = 0") == 5

def test_relearn():
    d = getEmptyCol()
    f = d.newNote()
    f['Front'] = "one"
    d.addNote(f)
    c = f.cards()[0]
    c.ivl = 100
    c.due = d.sched.today
    c.type = c.queue = QUEUE_REV
    c.flush()

    # fail the card
    d.reset()
    c = d.sched.getCard()
    d.sched.answerCard(c, 1)
    assert c.queue == QUEUE_LRN
    assert c.type == CARD_RELRN
    assert c.ivl == 1

    # immediately graduate it
    d.sched.answerCard(c, 4)
    assert c.queue == QUEUE_REV and c.type == CARD_DUE
    assert c.ivl == 2
    assert c.due == d.sched.today + c.ivl

def test_relearn_no_steps():
    d = getEmptyCol()
    f = d.newNote()
    f['Front'] = "one"
    d.addNote(f)
    c = f.cards()[0]
    c.ivl = 100
    c.due = d.sched.today
    c.type = c.queue = QUEUE_REV
    c.flush()

    conf = d.decks.get(1).getConf()
    conf['lapse']['delays'] = []
    conf.save()

    # fail the card
    d.reset()
    c = d.sched.getCard()
    d.sched.answerCard(c, 1)
    assert c.type == c.queue == QUEUE_REV

def test_learn_collapsed():
    d = getEmptyCol()
    # add 2 notes
    f = d.newNote()
    f['Front'] = "1"
    f = d.addNote(f)
    f = d.newNote()
    f['Front'] = "2"
    f = d.addNote(f)
    # set as a learn card and rebuild queues
    d.db.execute("update cards set queue=0, type=0")
    d.reset()
    # should get '1' first
    c = d.sched.getCard()
    assert c.q().endswith("1")
    # pass it so it's due in 10 minutes
    d.sched.answerCard(c, 3)
    # get the other card
    c = d.sched.getCard()
    assert c.q().endswith("2")
    # fail it so it's due in 1 minute
    d.sched.answerCard(c, 1)
    # we shouldn't get the same card again
    c = d.sched.getCard()
    assert not c.q().endswith("2")

def test_learn_day():
    d = getEmptyCol()
    # add a note
    f = d.newNote()
    f['Front'] = "one"
    f = d.addNote(f)
    d.sched.reset()
    c = d.sched.getCard()
    c.currentConf()['new']['delays'] = [1, 10, 1440, 2880]
    # pass it
    d.sched.answerCard(c, 3)
    # two reps to graduate, 1 more today
    assert c.left%1000 == 3
    assert c.left//1000 == 1
    assert d.sched.counts() == (0, 1, 0)
    c = d.sched.getCard()
    ni = d.sched.nextIvl
    assert ni(c, 3) == 86400
    # answering it will place it in queue 3
    d.sched.answerCard(c, 3)
    assert c.due == d.sched.today+1
    assert c.queue == QUEUE_DAY_LRN
    assert not d.sched.getCard()
    # for testing, move it back a day
    c.due -= 1
    c.flush()
    d.reset()
    assert d.sched.counts() == (0, 1, 0)
    c = d.sched.getCard()
    # nextIvl should work
    assert ni(c, 3) == 86400*2
    # if we fail it, it should be back in the correct queue
    d.sched.answerCard(c, 1)
    assert c.queue == QUEUE_LRN
    d.undo()
    d.reset()
    c = d.sched.getCard()
    d.sched.answerCard(c, 3)
    # simulate the passing of another two days
    c.due -= 2
    c.flush()
    d.reset()
    # the last pass should graduate it into a review card
    assert ni(c, 3) == 86400
    d.sched.answerCard(c, 3)
    assert c.queue == c.type == CARD_DUE
    # if the lapse step is tomorrow, failing it should handle the counts
    # correctly
    c.due = 0
    c.flush()
    d.reset()
    assert d.sched.counts() == (0, 0, 1)
    c.currentConf()['lapse']['delays'] = [1440]
    c = d.sched.getCard()
    d.sched.answerCard(c, 1)
    assert c.queue == QUEUE_DAY_LRN
    assert d.sched.counts() == (0, 0, 0)

def test_reviews():
    d = getEmptyCol()
    # add a note
    f = d.newNote()
    f['Front'] = "one"; f['Back'] = "two"
    d.addNote(f)
    # set the card up as a review card, due 8 days ago
    c = f.cards()[0]
    c.type = CARD_DUE
    c.queue = QUEUE_REV
    c.due = d.sched.today - 8
    c.factor = STARTING_FACTOR
    c.reps = 3
    c.lapses = 1
    c.ivl = 100
    c.startTimer()
    c.flush()
    # save it for later use as well
    cardcopy = copy.copy(c)
    # try with an ease of 2
    ##################################################
    c = copy.copy(cardcopy)
    c.flush()
    d.reset()
    d.sched.answerCard(c, 2)
    assert c.queue == QUEUE_REV
    # the new interval should be (100) * 1.2 = 120
    assert checkRevIvl(d, c, 120)
    assert c.due == d.sched.today + c.ivl
    # factor should have been decremented
    assert c.factor == 2350
    # check counters
    assert c.lapses == 1
    assert c.reps == 4
    # ease 3
    ##################################################
    c = copy.copy(cardcopy)
    c.flush()
    d.sched.answerCard(c, 3)
    # the new interval should be (100 + 8/2) * 2.5 = 260
    assert checkRevIvl(d, c, 260)
    assert c.due == d.sched.today + c.ivl
    # factor should have been left alone
    assert c.factor == STARTING_FACTOR
    # ease 4
    ##################################################
    c = copy.copy(cardcopy)
    c.flush()
    d.sched.answerCard(c, 4)
    # the new interval should be (100 + 8) * 2.5 * 1.3 = 351
    assert checkRevIvl(d, c, 351)
    assert c.due == d.sched.today + c.ivl
    # factor should have been increased
    assert c.factor == 2650
    # leech handling
    ##################################################
    c = copy.copy(cardcopy)
    c.lapses = 7
    c.flush()
    # steup hook
    hooked = []
    def onLeech(card):
        hooked.append(1)
    addHook("leech", onLeech)
    d.sched.answerCard(c, 1)
    assert hooked
    assert c.queue == QUEUE_SUSPENDED
    c.load()
    assert c.queue == QUEUE_SUSPENDED

def test_review_limits():
    d = getEmptyCol()

    parent = d.decks.byName("parent", create=True)
    child = d.decks.byName("parent::child", create=True)

    pconf = d.decks.newConf("parentConf")
    cconf = d.decks.newConf("childConf")

    pconf['rev']['perDay'] = 5
    pconf.update()
    parent.setConf(pconf.getId())
    cconf['rev']['perDay'] = 10
    cconf.update()
    child.setConf(cconf.getId())

    m = d.models.current()
    m['did'] = child.getId()
    m.save()

    # add some cards
    for i in range(20):
        f = d.newNote()
        f['Front'] = "one"; f['Back'] = "two"
        d.addNote(f)

        # make them reviews
        c = f.cards()[0]
        c.queue = c.type = CARD_DUE
        c.due = 0
        c.flush()

    d.reset()
    # (('Default', 1, 0, 0, 0, ()), ('parent', 1514457677462, 5, 0, 0, (('child', 1514457677463, 5, 0, 0, ()),)))
    d.sched.deckDueTree()
    assert parent.count['due']['rev'] == 5 # parent
    assert child.count['due']['rev'] == 5 # child

    # .counts() should match
    child.select()
    d.sched.reset()
    assert d.sched.counts() == (0, 0, 5)

    # answering a card in the child should decrement parent count
    c = d.sched.getCard()
    d.sched.answerCard(c, 3)
    assert d.sched.counts() == (0, 0, 4)

    d.sched.deckDueTree()
    assert parent.count['due']['rev'] == 4
    assert child.count['due']['rev'] == 4

    # switch limits
    parent.setConf(cconf.getId())
    child.setConf(pconf.getId())
    parent.select()
    d.sched.reset()

    # child limits do not affect the parent
    d.sched.deckDueTree()
    assert child.count['due']['rev'] == 4 # child
    assert parent.count['due']['rev'] == 9 # parent

def test_button_spacing():
    d = getEmptyCol()
    f = d.newNote()
    f['Front'] = "one"
    d.addNote(f)
    # 1 day ivl review card due now
    c = f.cards()[0]
    c.type = CARD_DUE
    c.queue = QUEUE_REV
    c.due = d.sched.today
    c.reps = 1
    c.ivl = 1
    c.startTimer()
    c.flush()
    d.reset()
    ni = d.sched.nextIvlStr
    assert ni(c, 2) == "2 days"
    assert ni(c, 3) == "3 days"
    assert ni(c, 4) == "4 days"

    # if hard factor is <= 1, then hard may not increase
    conf = d.decks.get(1).getConf()
    conf['rev']['hardFactor'] = 1
    assert ni(c, 2) == "1 day"

def test_overdue_lapse():
    # disabled in commit 3069729776990980f34c25be66410e947e9d51a2
    return
    d = getEmptyCol()
    # add a note
    f = d.newNote()
    f['Front'] = "one"
    d.addNote(f)
    # simulate a review that was lapsed and is now due for its normal review
    c = f.cards()[0]
    c.type = CARD_DUE
    c.queue = QUEUE_LRN
    c.due = -1
    c.odue = -1
    c.factor = STARTING_FACTOR
    c.left = 2002
    c.ivl = 0
    c.flush()
    d.sched._clearOverdue = False
    # checkpoint
    d.save()
    d.sched.reset()
    assert d.sched.counts() == (0, 2, 0)
    c = d.sched.getCard()
    d.sched.answerCard(c, 3)
    # it should be due tomorrow
    assert c.due == d.sched.today + 1
    # revert to before
    d.rollback()
    d.sched._clearOverdue = True
    # with the default settings, the overdue card should be removed from the
    # learning queue
    d.sched.reset()
    assert d.sched.counts() == (0, 0, 1)

def test_finished():
    d = getEmptyCol()
    # nothing due
    assert "Congratulations" in d.sched.finishedMsg()
    assert "limit" not in d.sched.finishedMsg()
    f = d.newNote()
    f['Front'] = "one"; f['Back'] = "two"
    d.addNote(f)
    # have a new card
    assert "new cards available" in d.sched.finishedMsg()
    # turn it into a review
    d.reset()
    c = f.cards()[0]
    c.startTimer()
    d.sched.answerCard(c, 3)
    # nothing should be due tomorrow, as it's due in a week
    assert "Congratulations" in d.sched.finishedMsg()
    assert "limit" not in d.sched.finishedMsg()

def test_nextIvl():
    d = getEmptyCol()
    f = d.newNote()
    f['Front'] = "one"; f['Back'] = "two"
    d.addNote(f)
    d.reset()
    conf = d.decks.get(1).getConf()
    conf['new']['delays'] = [0.5, 3, 10]
    conf['lapse']['delays'] = [1, 5, 9]
    c = d.sched.getCard()
    # new cards
    ##################################################
    ni = d.sched.nextIvl
    assert ni(c, 1) == 30
    assert ni(c, 2) == (30+180)//2
    assert ni(c, 3) == 180
    assert ni(c, 4) == 4*86400
    d.sched.answerCard(c, 1)
    # cards in learning
    ##################################################
    assert ni(c, 1) == 30
    assert ni(c, 2) == (30+180)//2
    assert ni(c, 3) == 180
    assert ni(c, 4) == 4*86400
    d.sched.answerCard(c, 3)
    assert ni(c, 1) == 30
    assert ni(c, 2) == (180+600)//2
    assert ni(c, 3) == 600
    assert ni(c, 4) == 4*86400
    d.sched.answerCard(c, 3)
    # normal graduation is tomorrow
    assert ni(c, 3) == 1*86400
    assert ni(c, 4) == 4*86400
    # lapsed cards
    ##################################################
    c.type = CARD_DUE
    c.ivl = 100
    c.factor = STARTING_FACTOR
    assert ni(c, 1) == 60
    assert ni(c, 3) == 100*86400
    assert ni(c, 4) == 101*86400
    # review cards
    ##################################################
    c.queue = QUEUE_REV
    c.ivl = 100
    c.factor = STARTING_FACTOR
    # failing it should put it at 60s
    assert ni(c, 1) == 60
    # or 1 day if relearn is false
    c.currentConf()['lapse']['delays']=[]
    assert ni(c, 1) == 1*86400
    # (* 100 1.2 86400)10368000.0
    assert ni(c, 2) == 10368000
    # (* 100 2.5 86400)21600000.0
    assert ni(c, 3) == 21600000
    # (* 100 2.5 1.3 86400)28080000.0
    assert ni(c, 4) == 28080000
    assert d.sched.nextIvlStr(c, 4) == "10.8 months"

def test_bury():
    d = getEmptyCol()
    f = d.newNote()
    f['Front'] = "one"
    d.addNote(f)
    c = f.cards()[0]
    f = d.newNote()
    f['Front'] = "two"
    d.addNote(f)
    c2 = f.cards()[0]
    # burying
    d.sched.buryCards([c.id], manual=True)
    c.load()
    assert c.queue == QUEUE_USER_BURIED
    d.sched.buryCards([c2.id], manual=False)
    c2.load()
    assert c2.queue == QUEUE_SCHED_BURIED

    d.reset()
    assert not d.sched.getCard()

    d.sched.unburyCardsForDeck(type="manual")
    c.load(); assert c.queue == QUEUE_NEW
    c2.load(); assert c2.queue == QUEUE_SCHED_BURIED

    d.sched.unburyCardsForDeck(type="siblings")
    c2.load(); assert c2.queue == QUEUE_NEW

    d.sched.buryCards([c.id, c2.id])
    d.sched.unburyCardsForDeck(type="all")

    d.reset()

    assert d.sched.counts() == (2, 0, 0)

def test_suspend():
    d = getEmptyCol()
    f = d.newNote()
    f['Front'] = "one"
    d.addNote(f)
    c = f.cards()[0]
    # suspending
    d.reset()
    assert d.sched.getCard()
    d.sched.suspendCards([c.id])
    d.reset()
    assert not d.sched.getCard()
    # unsuspending
    d.sched.unsuspendCards([c.id])
    d.reset()
    assert d.sched.getCard()
    # should cope with rev cards being relearnt
    c.due = 0; c.ivl = 100; c.type = CARD_DUE; c.queue = QUEUE_REV; c.flush()
    d.reset()
    c = d.sched.getCard()
    d.sched.answerCard(c, 1)
    assert c.due >= time.time()
    due = c.due
    assert c.queue == QUEUE_LRN
    assert c.type == CARD_RELRN
    d.sched.suspendCards([c.id])
    d.sched.unsuspendCards([c.id])
    c.load()
    assert c.queue == QUEUE_LRN
    assert c.type == CARD_RELRN
    assert c.due == due
    # should cope with cards in cram decks
    c.due = 1
    c.flush()
    d.decks.newDyn("tmp")
    d.decks.current().rebuildDyn()
    c.load()
    assert c.due != 1
    assert c.did != 1
    d.sched.suspendCards([c.id])
    c.load()
    assert c.due != 1
    assert c.did != 1
    assert c.odue == 1

def test_filt_reviewing_early_normal():
    d = getEmptyCol()
    f = d.newNote()
    f['Front'] = "one"
    d.addNote(f)
    c = f.cards()[0]
    c.ivl = 100
    c.type = c.queue = QUEUE_REV
    # due in 25 days, so it's been waiting 75 days
    c.due = d.sched.today + 25
    c.mod = 1
    c.factor = STARTING_FACTOR
    c.startTimer()
    c.flush()
    d.reset()
    assert d.sched.counts() == (0,0,0)
    # create a dynamic deck and refresh it
    deck = d.decks.newDyn("Cram")
    did = deck.getId()
    deck.rebuildDyn()
    d.reset()
    # should appear as normal in the deck list
    d.sched.deckDueTree()
    assert d.decks.all(sort=True)[0].count['singleDue']['rev'] == 1
    # and should appear in the counts
    assert d.sched.counts() == (0,0,1)
    # grab it and check estimates
    c = d.sched.getCard()
    assert d.sched.answerButtons(c) == 4
    assert d.sched._lapseConf(c)['delays'][0] == 10
    assert d.sched.nextIvl(c, 1) == 600
    assert d.sched.nextIvl(c, 2) == int(75*1.2)*86400
    assert d.sched.nextIvl(c, 3) == int(75*2.5)*86400
    assert d.sched.nextIvl(c, 4) == int(75*2.5*1.15)*86400

    # answer 'good'
    d.sched.answerCard(c, 3)
    checkRevIvl(d, c, 90)
    assert c.due == d.sched.today + c.ivl
    assert not c.odue
    # should not be in learning
    assert c.queue == QUEUE_REV
    # should be logged as a cram rep
    assert d.db.scalar(
        "select type from revlog order by id desc limit 1") == 3

    # due in 75 days, so it's been waiting 25 days
    c.ivl = 100
    c.due = d.sched.today + 75
    c.flush()
    deck.rebuildDyn()
    d.reset()
    c = d.sched.getCard()

    assert d.sched.nextIvl(c, 2) == 60*86400
    assert d.sched.nextIvl(c, 3) == 100*86400
    assert d.sched.nextIvl(c, 4) == 114*86400

def test_filt_keep_lrn_state():
    d = getEmptyCol()

    f = d.newNote()
    f['Front'] = "one"
    d.addNote(f)

    # fail the card outside filtered deck
    c = d.sched.getCard()
    c.currentConf()['new']['delays'] = [1, 10, 61]
    d.decks.save()

    d.sched.answerCard(c, 1)

    assert c.type == CARD_LRN
    assert c.queue == QUEUE_LRN
    assert c.left == 3003

    d.sched.answerCard(c, 3)
    assert c.type == CARD_LRN
    assert c.queue == QUEUE_LRN

    # create a dynamic deck and refresh it
    deck = d.decks.newDyn("Cram")
    did = deck.getId()
    deck.rebuildDyn()
    d.reset()

    # card should still be in learning state
    c.load()
    assert c.type == CARD_LRN
    assert c.queue == QUEUE_LRN
    assert c.left == 2002

    # should be able to advance learning steps
    d.sched.answerCard(c, 3)
    # should be due at least an hour in the future
    assert c.due - intTime() > 60*60

    # emptying the deck preserves learning state
    d.sched.emptyDyn(deck)
    c.load()
    assert c.type == CARD_LRN
    assert c.queue == QUEUE_LRN
    assert c.left == 1001
    assert c.due - intTime() > 60*60

def test_preview():
    # add cards
    d = getEmptyCol()
    f = d.newNote()
    f['Front'] = "one"
    d.addNote(f)
    c = f.cards()[0]
    orig = copy.copy(c)
    f2 = d.newNote()
    f2['Front'] = "two"
    d.addNote(f2)
    # cram deck
    deck = d.decks.newDyn("Cram")
    did = deck.getId()
    cram = d.decks.get(did)
    cram['resched'] = False
    deck.rebuildDyn()
    d.reset()
    # grab the first card
    c = d.sched.getCard()
    assert d.sched.answerButtons(c) == 2
    assert d.sched.nextIvl(c, 1) == 600
    assert d.sched.nextIvl(c, 2) == 0
    # failing it will push its due time back
    due = c.due
    d.sched.answerCard(c, 1)
    assert c.due != due

    # the other card should come next
    c2 = d.sched.getCard()
    assert c2.id != c.id

    # passing it will remove it
    d.sched.answerCard(c2, 2)
    assert c2.queue == QUEUE_NEW
    assert c2.reps == 0
    assert c2.type == 0

    # the other card should appear again
    c = d.sched.getCard()
    assert c.id == orig.id

    # emptying the filtered deck should restore card
    d.sched.emptyDyn(deck)
    c.load()
    assert c.queue == QUEUE_NEW
    assert c.reps == 0
    assert c.type == 0

def test_ordcycle():
    d = getEmptyCol()
    # add two more templates and set second active
    m = d.models.current(); mm = d.models
    t = m.newTemplate("Reverse",
                      "{{Back}}",
                      "{{Front}}")
    t = m.newTemplate("f2",
                      "{{Front}}",
                      "{{Back}}")
    m.save()
    # create a new note; it should have 3 cards
    f = d.newNote()
    f['Front'] = "1"; f['Back'] = "1"
    d.addNote(f)
    assert d.cardCount() == 3
    d.reset()
    # ordinals should arrive in order
    assert d.sched.getCard().ord == 0
    assert d.sched.getCard().ord == 1
    assert d.sched.getCard().ord == 2

def test_counts_idx():
    d = getEmptyCol()
    f = d.newNote()
    f['Front'] = "one"; f['Back'] = "two"
    d.addNote(f)
    d.reset()
    assert d.sched.counts() == (1, 0, 0)
    c = d.sched.getCard()
    # counter's been decremented but idx indicates 1
    assert d.sched.counts() == (0, 0, 0)
    assert d.sched.countIdx(c) == 0
    # answer to move to learn queue
    d.sched.answerCard(c, 1)
    assert d.sched.counts() == (0, 1, 0)
    # fetching again will decrement the count
    c = d.sched.getCard()
    assert d.sched.counts() == (0, 0, 0)
    assert d.sched.countIdx(c) == 1
    # answering should add it back again
    d.sched.answerCard(c, 1)
    assert d.sched.counts() == (0, 1, 0)

def test_repCounts():
    d = getEmptyCol()
    f = d.newNote()
    f['Front'] = "one"
    d.addNote(f)
    d.reset()
    # lrnReps should be accurate on pass/fail
    assert d.sched.counts() == (1, 0, 0)
    d.sched.answerCard(d.sched.getCard(), 1)
    assert d.sched.counts() == (0, 1, 0)
    d.sched.answerCard(d.sched.getCard(), 1)
    assert d.sched.counts() == (0, 1, 0)
    d.sched.answerCard(d.sched.getCard(), 3)
    assert d.sched.counts() == (0, 1, 0)
    d.sched.answerCard(d.sched.getCard(), 1)
    assert d.sched.counts() == (0, 1, 0)
    d.sched.answerCard(d.sched.getCard(), 3)
    assert d.sched.counts() == (0, 1, 0)
    d.sched.answerCard(d.sched.getCard(), 3)
    assert d.sched.counts() == (0, 0, 0)
    f = d.newNote()
    f['Front'] = "two"
    d.addNote(f)
    d.reset()
    # initial pass should be correct too
    d.sched.answerCard(d.sched.getCard(), 3)
    assert d.sched.counts() == (0, 1, 0)
    d.sched.answerCard(d.sched.getCard(), 1)
    assert d.sched.counts() == (0, 1, 0)
    d.sched.answerCard(d.sched.getCard(), 4)
    assert d.sched.counts() == (0, 0, 0)
    # immediate graduate should work
    f = d.newNote()
    f['Front'] = "three"
    d.addNote(f)
    d.reset()
    d.sched.answerCard(d.sched.getCard(), 4)
    assert d.sched.counts() == (0, 0, 0)
    # and failing a review should too
    f = d.newNote()
    f['Front'] = "three"
    d.addNote(f)
    c = f.cards()[0]
    c.type = CARD_DUE
    c.queue = QUEUE_REV
    c.due = d.sched.today
    c.flush()
    d.reset()
    assert d.sched.counts() == (0, 0, 1)
    d.sched.answerCard(d.sched.getCard(), 1)
    assert d.sched.counts() == (0, 1, 0)

def test_timing():
    d = getEmptyCol()
    # add a few review cards, due today
    for i in range(5):
        f = d.newNote()
        f['Front'] = "num"+str(i)
        d.addNote(f)
        c = f.cards()[0]
        c.type = CARD_DUE
        c.queue = QUEUE_REV
        c.due = 0
        c.flush()
    # fail the first one
    d.reset()
    c = d.sched.getCard()
    d.sched.answerCard(c, 1)
    # the next card should be another review
    c2 = d.sched.getCard()
    assert c2.queue == QUEUE_REV
    # if the failed card becomes due, it should show first
    c.due = time.time() - 1
    c.flush()
    d.reset()
    c = d.sched.getCard()
    assert c.queue == QUEUE_LRN

def test_collapse():
    d = getEmptyCol()
    # add a note
    f = d.newNote()
    f['Front'] = "one"
    d.addNote(f)
    d.reset()
    # test collapsing
    c = d.sched.getCard()
    d.sched.answerCard(c, 1)
    c = d.sched.getCard()
    d.sched.answerCard(c, 4)
    assert not d.sched.getCard()

def test_deckDue():
    d = getEmptyCol()
    # add a note with default deck
    f = d.newNote()
    f['Front'] = "one"
    d.addNote(f)
    # and one that's a child
    f = d.newNote()
    f['Front'] = "two"
    default1 = f.model()['did'] = d.decks.id("Default::1")
    d.addNote(f)
    # make it a review card
    c = f.cards()[0]
    c.queue = QUEUE_REV
    c.due = 0
    c.flush()
    # add one more with a new deck
    f = d.newNote()
    f['Front'] = "two"
    foobar = f.model()['did'] = d.decks.id("foo::bar")
    d.addNote(f)
    # and one that's a sibling
    f = d.newNote()
    f['Front'] = "three"
    foobaz = f.model()['did'] = d.decks.id("foo::baz")
    d.addNote(f)
    d.reset()
    assert len(d.decks.all()) == 5
    d.sched.deckDueTree()
    cnts = d.decks.all(sort=True)
    def l(deck):
        return [deck.getPath(), deck.getId(), deck.count['singleDue']['rev'], deck.count['singleDue']['lrn'], deck.count['singleDue']['new']]
    assert l(cnts[0]) == [["Default"], 1, 1, 0, 1]
    assert l(cnts[1]) == [["Default", "1"], default1, 1, 0, 0]
    assert l(cnts[2]) == [["foo"], d.decks.id("foo"), 0, 0, 0]
    assert l(cnts[3]) == [["foo", "bar"], foobar, 0, 0, 1]
    assert l(cnts[4]) == [["foo", "baz"], foobaz, 0, 0, 1]
    d.sched.deckDueTree()
    tree = d.decks.all(sort=True)
    assert tree[0].getBaseName() == "Default"
    # sum of child and parent
    assert tree[0].getId() == 1
    assert tree[0].count['due']['rev'] == 1
    assert tree[0].count['due']['new'] == 1
    # child count is just review
    assert tree[0].getChildren()[0].getBaseName() == "1"
    assert tree[0].getChildren()[0].getId() == default1
    assert tree[0].getChildren()[0].count['due']['rev'] == 1
    assert tree[0].getChildren()[0].count['due']['new'] == 0
    # code should not fail if a card has an invalid deck
    c.did = 12345; c.flush()

def test_deckTree():
    d = getEmptyCol()
    d.decks.id("new::b::c")
    d.decks.id("new2")
    # new should not appear twice in tree
    names = d.decks.allNames()
    names.remove("new")
    assert "new" not in names

def test_deckFlow():
    d = getEmptyCol()
    # add a note with default deck
    f = d.newNote()
    f['Front'] = "one"
    d.addNote(f)
    # and one that's a child
    f = d.newNote()
    f['Front'] = "two"
    default1 = f.model()['did'] = d.decks.id("Default::2")
    d.addNote(f)
    # and another that's higher up
    f = d.newNote()
    f['Front'] = "three"
    default1 = f.model()['did'] = d.decks.id("Default::1")
    d.addNote(f)
    # should get top level one first, then ::1, then ::2
    d.reset()
    assert d.sched.counts() == (3,0,0)
    for i in "one", "three", "two":
        c = d.sched.getCard()
        assert c.note()['Front'] == i
        d.sched.answerCard(c, 3)

def test_reorder():
    d = getEmptyCol()
    # add a note with default deck
    f = d.newNote()
    f['Front'] = "one"
    d.addNote(f)
    f2 = d.newNote()
    f2['Front'] = "two"
    d.addNote(f2)
    assert f2.cards()[0].due == 2
    found=False
    # 50/50 chance of being reordered
    defaultDeck = d.decks.get(1)
    for i in range(20):
        defaultDeck.randomizeCards()
        if f.cards()[0].due != f.id:
            found=True
            break
    assert found
    defaultDeck.orderCards()
    assert f.cards()[0].due == 1
    # shifting
    f3 = d.newNote()
    f3['Front'] = "three"
    d.addNote(f3)
    f4 = d.newNote()
    f4['Front'] = "four"
    d.addNote(f4)
    assert f.cards()[0].due == 1
    assert f2.cards()[0].due == 2
    assert f3.cards()[0].due == 3
    assert f4.cards()[0].due == 4
    d.sched.sortCards([
        f3.cards()[0].id, f4.cards()[0].id], start=1, shift=True)
    assert f.cards()[0].due == 3
    assert f2.cards()[0].due == 4
    assert f3.cards()[0].due == 1
    assert f4.cards()[0].due == 2

def test_forget():
    d = getEmptyCol()
    f = d.newNote()
    f['Front'] = "one"
    d.addNote(f)
    c = f.cards()[0]
    c.queue = QUEUE_REV; c.type = CARD_DUE; c.ivl = 100; c.due = 0
    c.flush()
    d.reset()
    assert d.sched.counts() == (0, 0, 1)
    d.sched.forgetCards([c.id])
    d.reset()
    assert d.sched.counts() == (1, 0, 0)

def test_resched():
    d = getEmptyCol()
    f = d.newNote()
    f['Front'] = "one"
    d.addNote(f)
    c = f.cards()[0]
    d.sched.reschedCards([c.id], 0, 0)
    c.load()
    assert c.due == d.sched.today
    assert c.ivl == 1
    assert c.queue == c.type == CARD_DUE
    d.sched.reschedCards([c.id], 1, 1)
    c.load()
    assert c.due == d.sched.today+1
    assert c.ivl == +1

def test_norelearn():
    d = getEmptyCol()
    # add a note
    f = d.newNote()
    f['Front'] = "one"
    d.addNote(f)
    c = f.cards()[0]
    c.type = CARD_DUE
    c.queue = QUEUE_REV
    c.due = 0
    c.factor = STARTING_FACTOR
    c.reps = 3
    c.lapses = 1
    c.ivl = 100
    c.startTimer()
    c.flush()
    d.reset()
    d.sched.answerCard(c, 1)
    c.currentConf()['lapse']['delays'] = []
    d.sched.answerCard(c, 1)

def test_failmult():
    d = getEmptyCol()
    f = d.newNote()
    f['Front'] = "one"; f['Back'] = "two"
    d.addNote(f)
    c = f.cards()[0]
    c.type = CARD_DUE
    c.queue = QUEUE_REV
    c.ivl = 100
    c.due = d.sched.today - c.ivl
    c.factor = STARTING_FACTOR
    c.reps = 3
    c.lapses = 1
    c.startTimer()
    c.flush()
    c.currentConf()['lapse']['mult'] = 0.5
    c = d.sched.getCard()
    d.sched.answerCard(c, 1)
    assert c.ivl == 50
    d.sched.answerCard(c, 1)
    assert c.ivl == 25

def test_moveVersions():
    col = getEmptyCol()
    col.changeSchedulerVer(1)

    n = col.newNote()
    n['Front'] = "one"
    col.addNote(n)

    # make it a learning card
    col.reset()
    c = col.sched.getCard()
    col.sched.answerCard(c, 1)

    # the move to v2 should reset it to new
    col.changeSchedulerVer(2)
    c.load()
    assert c.queue == QUEUE_NEW
    assert c.type == 0

    # fail it again, and manually bury it
    col.reset()
    c = col.sched.getCard()
    col.sched.answerCard(c, 1)
    col.sched.buryCards([c.id])
    c.load()
    assert c.queue == QUEUE_USER_BURIED

    # revert to version 1
    col.changeSchedulerVer(1)

    # card should have moved queues
    c.load()
    assert c.queue == QUEUE_SCHED_BURIED

    # and it should be new again when unburied
    col.sched.unburyCards()
    c.load()
    assert c.queue == QUEUE_NEW
    assert c.type == CARD_NEW

    # make sure relearning cards transition correctly to v1
    col.changeSchedulerVer(2)
    # card with 100 day interval, answering again
    col.sched.reschedCards([c.id], 100, 100)
    c.load(); c.due = 0; c.flush()
    c.currentConf()['lapse']['mult'] = 0.5
    col.sched.reset()
    c = col.sched.getCard()
    col.sched.answerCard(c, 1)
    # due should be correctly set when removed from learning early
    col.changeSchedulerVer(1)
    c.load()
    assert c.due == 50

# cards with a due date earlier than the collection should retain
# their due date when removed
def test_negativeDueFilter():
    d = getEmptyCol()

    # card due prior to collection date
    f = d.newNote()
    f['Front'] = "one"; f['Back'] = "two"
    d.addNote(f)
    c = f.cards()[0]
    c.due = -5
    c.queue = QUEUE_REV
    c.ivl = 5
    c.flush()

    # into and out of filtered deck
    deck = d.decks.newDyn("Cram")
    did = deck.getId()
    deck.rebuildDyn()
    d.sched.emptyDyn(deck)
    d.reset()

    c.load()
    assert c.due == -5
