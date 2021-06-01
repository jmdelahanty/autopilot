"""

Classes that manage hardware logic.

Each hardware class should be able to operate independently - ie. not
be dependent on a particular task class, etc. Other than that there are
very few design requirements:

* Every class should have a .release() method that releases any system
  resources in use by the object, eg. objects that use pigpio must have
  their `pigpio.pi` client stopped; LEDs should be explicitly turned off.
* The very minimal class attributes are described in the :class:`Hardware` metaclass.
* Hardware methods are typically called in their own threads, so care should
  be taken to make any long-running operations internally threadsafe.

.. _numbering-note:

Note:
    This software was primarily developed for the Raspberry Pi, which
    has `two types of numbering schemes <https://pinout.xyz/#>`_ ,
    "board" numbering based on physical position (e.g. pins 1-40, in 2 rows of 20 pins) and "bcm" numbering
    based on the broadcom chip numbering scheme (e.g. GPIO2, GPIO27).

    Board numbering is easier to use, but `pigpio <http://abyz.me.uk/rpi/pigpio/>`_
    , which we use as a bridge between Python and the GPIOs, uses the BCM scheme.
    As such each class that uses the GPIOs takes a board number as its argument
    and converts it to a BCM number in the __init__ method.

    If there is sufficient demand to make this more flexible, we can implement
    an additional `pref` to set the numbering scheme, but the current solution
    works without getting too muddy.

Warning:
    In order to use pigpio, the pigpio daemon must be running. See `the docs <http://abyz.me.uk/rpi/pigpio/python.html>`_
    Usually :class:`~.core.pilot.Pilot` s should be started by the bash script or systemd service
    generated by :mod:`.setup.setup_pilot`, which starts pigpiod.
"""


from autopilot import prefs
from autopilot.networking import Net_Node
from autopilot.core.loggers import init_logger

# FIXME: Hardcoding names of metaclasses, should have some better system of denoting which classes can be instantiated
# directly for setup and prefs management.
META_CLASS_NAMES = ['Hardware', 'Camera', 'GPIO', 'Directory_Writer', 'Video_Writer']


# pigpio only uses BCM numbers, we need to translate them
# See https://www.element14.com/community/servlet/JiveServlet/previewBody/73950-102-11-339300/pi3_gpio.png
BOARD_TO_BCM = {
     3: 2,   5: 3,   7: 4,   8: 14, 10: 15,
    11: 17, 12: 18, 13: 27, 15: 22, 16: 23,
    18: 24, 19: 10, 21: 9,  22: 25, 23: 11,
    24: 8,  26: 7,  29: 5,  31: 6,  32: 12,
    33: 13, 35: 19, 36: 16, 37: 26, 38: 20,
    40: 21
}
"""
dict: Mapping from board (physical) numbering to BCM numbering. 

See `this pinout <https://pinout.xyz/#>`_.

Hardware objects take board numbered pins and convert them to BCM 
numbers for use with `pigpio`.
"""

BCM_TO_BOARD = dict([reversed(i) for i in BOARD_TO_BCM.items()])
"""
dict: The inverse of :const:`BOARD_TO_BCM`.
"""


class Hardware(object):
    """
    Generic class inherited by all hardware. Should not be instantiated
    on its own (but it won't do anything bad so go nuts i guess).

    Primarily for the purpose of defining necessary attributes.

    Also defines `__del__` to call `release()` so objects are always released
    even if not explicitly.

    Attributes:
        is_trigger (bool): Is this object a discrete event input device?
            or, will this device be used to trigger some event? If `True`,
            will be given a callback by :class:`.Task`, and :meth:`.assign_cb`
            must be redefined.
        pin (int): The BCM pin used by this device, or None if no pin is used.
        type (str): What is this device known as in `.prefs`? Not required.
        input (bool): Is this an input device?
        output (bool): Is this an output device?
    """
    # metaclass for hardware objects
    is_trigger = False
    pin = None
    type = "" # what are we known as in prefs?
    input = False
    output = False

    def __init__(self, name=None, **kwargs):
        if name:
            self.name = name
        else:
            try:
                self.name = self.get_name()
            except:
                Warning('wasnt passed name and couldnt find from prefs for object: {}'.format(self.__str__))
                self.name = None

        self.logger = init_logger(self)
        self.listens = {}
        self.node = None

    def release(self):
        """
        Every hardware device needs to redefine `release()`, and must

        * Safely unload any system resources used by the object, and
        * Return the object to a neutral state - eg. LEDs turn off.

        When not redefined, a warning is given.
        """
        Exception('The release method was not overridden by the subclass!')

    def assign_cb(self, trigger_fn):
        """
        Every hardware device that is a :attr:`~Hardware.trigger` must redefine this
        to accept a function (typically :meth:`.Task.handle_trigger`) that
        is called when that trigger is activated.

        When not redefined, a warning is given.
        """
        if self.is_trigger:
            Exception("The assign_cb method was not overridden by the subclass!")

    def get_name(self):
        """
        Usually Hardware is only instantiated with its pin number,
        but we can get its name from prefs
        """

        # TODO: Unify identification of hardware types across prefs and hardware objects
        try:
            our_type = prefs.get('HARDWARE')[self.type]
        except KeyError:
            our_type = prefs.get('HARDWARE')[self.__class__.__name__]

        for name, pin in our_type.items():
            if self.pin == pin:
                return name
            elif isinstance(pin, dict):
                if self.pin == pin['pin']:
                    return name

    def init_networking(self, listens=None, **kwargs):
        """
        Spawn a :class:`.Net_Node` to :attr:`Hardware.node` for streaming or networked command

        Args:
            listens (dict): Dictionary mapping message keys to handling methods
            **kwargs: Passed to :class:`.Net_Node`

        Returns:

        """

        if not listens:
            listens = self.listens

        self.node = Net_Node(
            self.name,
            upstream=prefs.get('NAME'),
            port=prefs.get('MSGPORT'),
            listens=listens,
            instance=False,
            **kwargs
            #upstream_ip=prefs.get('TERMINALIP'),
            #daemon=False
        )



    def __del__(self):
        self.release()