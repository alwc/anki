import copy

from anki.consts import *
from anki.lang import _
from anki.utils import DictAugmentedDyn, intTime

defaultConf = {
    'name': _("Default"),
    'new': {
        'delays': [1, 10],
        'ints': [1, 4, 7], # 7 is not currently used
        'initialFactor': STARTING_FACTOR,
        'separate': True,
        'order': NEW_CARDS_DUE,
        'perDay': 20,
        # may not be set on old decks
        'bury': False,
    },
    'lapse': {
        'delays': [10],
        'mult': 0,
        'minInt': 1,
        'leechFails': 8,
        # type 0=suspend, 1=tagonly
        'leechAction': LEECH_SUSPEND,
    },
    'rev': {
        'perDay': 200,
        'ease4': 1.3,
        'fuzz': 0.05,
        'minSpace': 1, # not currently used
        'ivlFct': 1,
        'maxIvl': 36500,
        # may not be set on old decks
        'bury': False,
        'hardFactor': 1.2,
    },
    'maxTaken': 60,
    'timer': 0,
    'autoplay': True,
    'replayq': True,
    'mod': 0,
    'usn': 0,
}


class DConf(DictAugmentedDyn):
    """A configuration for decks

    """
    def load(self, manager, dict):
        super().load(manager, dict)
        # set limits to within bounds
        for type in ('rev', 'new'):
            pd = 'perDay'
            if self[type][pd] > 999999:
                self[type][pd] = 999999
                self.save()
                self.manager.changed = True

    # Basic tests
    #############################################################
    def isDefault(self):
        return str(self.getId()) == "1"

    def update(self):
        """Add g to the set of dconf's. Potentially replacing a dconf with the
same id."""
        self.manager.dconf[str(self.getId())] = self
        self.manager.save()

    def copy_(self, name):
        """Create a new configuration and return its id.

        Keyword arguments
        cloneFrom -- The configuration copied by the new one."""
        conf = self.deepcopy()
        while 1:
            id = intTime(1000)
            if str(id) not in self.manager.dconf:
                break
        conf['id'] = id
        conf.setName(name)
        self.manager.dconf[str(id)] = conf
        conf.save()
        return conf

    def rem(self):
        """Remove a configuration and update all decks using it.

        The new conf of the deck using this configuation is the
        default one.

        Keyword arguments:
        id -- The id of the configuration to remove. Should not be the
        default conf."""
        assert not self.isDefault()
        self.manager.col.modSchema(check=True)
        del self.manager.dconf[self.getId()]
        for deck in self.decks():
            deck.setDefaultConf()
            deck.save()

    def restoreToDefault(self):
        """Change the configuration to default.

        The only remaining part of the configuration are: the order of
        new card, the name and the id.
        """
        oldOrder = self['new']['order']
        new = copy.deepcopy(defaultConf)
        new['id'] = self.getId()
        new.setName(self.getName())
        self.manager.dconf[str(self.getId())] = new
        new.save()
        # if it was previously randomized, resort
        if not oldOrder:
            new.resortConf()

    def getDecks(self, conf):
        """The decks of the decks using the configuration conf."""
        return [deck for deck in self.decks.values() if 'conf' in deck and deck.getConfId() == conf.getId()]

    def getDids(self, conf):
        """The dids of the decks using the configuration conf."""
        return [deck.getId() for deck in self.decks()]

    # schedulers method
    ##########################################################

    def resortConf(self):
        for deck in self.getDecks():
            if conf['new']['order'] == NEW_CARDS_RANDOM:
                deck.randomizeCards()
            else:
                deck.orderCards()
