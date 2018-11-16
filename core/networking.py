# Classes for network communication.
# Following http://zguide.zeromq.org/py:all#toc46

import json
import logging
import threading
import zmq
import time
import sys
import datetime
import os
import multiprocessing
import base64
import socket
from zmq.eventloop.ioloop import IOLoop, PeriodicCallback
from zmq.eventloop.zmqstream import ZMQStream
from warnings import warn
from collections import deque
from itertools import count

# Message structure:
# Messages flow from the Terminal class to the raspberry pi
# {key: {target, value}}
# key - what type of message is this (byte string)
# target - where should it go (json encoded on receipt, converted to byte string)
#          For the Pilot, target refers to the mouse so the Terminal can plot/store data
# value - what is the message (json encoded)
# This structure allows us to sensibly 'unwrap' messages:
# the handler function for each socket passes the target and value to the appropriate function for a given key
# the key function will do whatever it needs to do with value and send it on to target

# Listens

# TODO: Periodically ping pis to check that they are still responsive

class Networking(multiprocessing.Process):
    ctx          = None    # Context
    loop         = None    # IOLoop
    push_ip      = None    # IP to push to
    push_port    = None    # Publisher Port
    listen_port  = None    # Listener Port
    message_port = None    # Messenger Port
    pusher       = None    # pusher socket - a dealer socket that connects to other routers
    listener     = None    # Listener socket - a router socket to send/recv messages
    logger       = None    # Logger....
    log_handler  = None
    log_formatter = None
    id           = None    # What are we known as?
    ip           = None    # whatismy
    listens      = None    # Dictionary of functions to call for different types of messages
    subscribers  = {}

    def __init__(self, prefs=None):
        super(Networking, self).__init__()
        # Prefs should be passed by the terminal, if not, try to load from default locatio
        # QtCore.QThread.__init__(self)
        if not prefs:
            try:
                with open('/usr/rpilot/prefs.json') as op:
                    self.prefs = json.load(op)
            except:
                Exception('No Prefs for networking class')
        else:
            self.prefs = prefs

        # # Store some prefs values
        # self.pub_port = self.prefs['PUBPORT']
        # self.listen_port = self.prefs['LISTENPORT']
        # self.message_port = self.prefs['MSGPORT']

        self.ip = self.get_ip()

        # Setup logging
        self.init_logging()

        self.file_block = threading.Event() # to wait for file transfer

        # number messages as we send them
        self.msg_counter = count()



    def run(self):
        self.init_logging()

        # init zmq objects
        self.context = zmq.Context()
        self.loop = IOLoop()

        # want one dealer to make requests and one router to reply
        self.listener  = self.context.socket(zmq.ROUTER)

        if self.pusher is True:
            self.pusher = self.context.socket(zmq.DEALER)
            self.pusher.setsockopt(zmq.IDENTITY, bytes(self.id))
            self.pusher.connect('tcp://{}:{}'.format(self.push_ip, self.push_port))
            self.pusher = ZMQStream(self.pusher, self.loop)
            self.pusher.on_recv(self.handle_listen)
            # TODO: Make sure handle_listen knows how to handle ID-less messages

        self.listener.setsockopt(zmq.IDENTITY, bytes(self.id))
        self.listener.bind('tcp://*:{}'.format(self.listen_port))
        self.listener = ZMQStream(self.listener, self.loop)

        self.listener.on_recv(self.handle_listen)

        self.logger.info('Starting IOLoop')
        self.loop.start()

    def send(self, target, msg):
        # send message via the router
        # don't need to thread this because router sends are nonblocking
        # Give the message a number and TTL, stash it
        msg_num = str(self.msg_counter.next())
        msg['id'] = msg_num
        msg['target'] = target

        # Make sure our message has everything
        if not all(i in msg.keys() for i in ['key', 'value']):
            self.logger.warning('SEND {} - Improperly formatted: {}'.format(msg_num, msg))
            return

        # check that we can encode
        try:
            msg_enc = json.dumps(msg)
        except:
            self.logger.error('SEND {} - Could not encode msg {}'.format(msg_num, msg))
            return

        self.listener.send_multipart([bytes(target), msg_enc])

    def push(self, msg):
        # send message via the dealer
        try:
            msg_enc = json.dumps(msg)
        except:
            self.logger.error('Could not encode msg {}'.format(msg))
            return

        self.listener.send(msg_enc)

    def handle_listen(self, msg, suppress_print=False):
        # listens are always json encoded, single-part messages
        if len(msg)==1:
            # from our dealer
            msg = json.loads(msg[0])
        elif len(msg)==2:
            # from the router
            sender = msg[0]
            msg    = json.loads(msg[1])

        # Check if our listen was sent properly
        if not all(i in msg.keys() for i in ['key','value']):
            logging.warning('LISTEN Improperly formatted: {}'.format(msg))
            return

        if msg['key'] == 'FILE':
            suppress_print = True

        if not suppress_print:
            self.logger.info('LISTEN {} - KEY: {}, VALUE: {}'.format(msg['id'], msg['key'], msg['value']))
        else:
            self.logger.info('LISTEN {} - KEY: {}'.format(msg['id'], msg['key']))

        # Log and spawn thread to respond to listen
        listen_funk = self.listens[msg['key']]
        listen_thread = threading.Thread(target=listen_funk, args=(msg['value'],))
        listen_thread.start()


    def init_logging(self):

        # Setup logging
        timestr = datetime.datetime.now().strftime('%y%m%d_%H%M%S')
        log_file = os.path.join(self.prefs['LOGDIR'], 'Networking_Log_{}.log'.format(timestr))

        self.logger = logging.getLogger('networking')
        self.log_handler = logging.FileHandler(log_file)
        self.log_formatter = logging.Formatter("%(asctime)s %(levelname)s : %(message)s")
        self.log_handler.setFormatter(self.log_formatter)
        self.logger.addHandler(self.log_handler)
        self.logger.setLevel(logging.INFO)
        self.logger.info('Networking Logging Initiated')

    def get_ip(self):
        # shamelessly stolen from https://www.w3resource.com/python-exercises/python-basic-exercise-55.php
        # variables are badly named because this is just a rough unwrapping of what was a monstrous one-liner

        # get ips that aren't the loopback
        unwrap00 = [ip for ip in socket.gethostbyname_ex(socket.gethostname())[2] if not ip.startswith("127.")][:1]
        # ??? truly dk
        unwrap01 = [[(s.connect(('8.8.8.8', 53)), s.getsockname()[0], s.close()) for s in
                     [socket.socket(socket.AF_INET, socket.SOCK_DGRAM)]][0][1]]

        unwrap2 = [l for l in (unwrap00, unwrap01) if l][0][0]

        return unwrap2

class Terminal_Networking(Networking):
    def __init__(self, prefs=None):
        if not prefs:
            try:
                with open('/usr/rpilot/prefs.json') as op:
                    self.prefs = json.load(op)
            except:
                Exception('No Prefs for networking class')
        else:
            self.prefs = prefs

        super(Terminal_Networking, self).__init__(self.prefs)

        # by default terminal doesn't have a pusher, everything connects to it
        self.pusher = False

        # Store some prefs values
        self.listen_port = self.prefs['LISTENPORT']
        self.id = b'T'

        # Message dictionary - What method to call for each type of message received by the terminal class
        self.listens = {
            'PING':      self.m_ping,  # We are asked to confirm that the raspberry pis are alive
            'INIT':      self.m_init,  # We should ask all the pilots to confirm that they are alive
            'START':     self.m_start_task,  # Upload task to a pilot
            'CHANGE':    self.m_change,  # Change a parameter on the Pi
            'STOP':      self.m_stop,
            'STOPALL':   self.m_stopall,
            'LISTENING': self.m_listening,  # Terminal wants to know if we're alive yet
            'KILL':      self.m_kill,  # Terminal wants us to die :(
            'DATA':      self.l_data,  # Stash incoming data from an rpilot
            'ALIVE':     self.l_alive,  # A Pi is responding to our periodic query of whether it remains alive
            # It replies with its subscription filter
            'STATE':     self.l_state,  # The Pi is confirming/notifying us that it has changed state
            'FILE':      self.l_file,  # The pi needs some file from us
        }


    ##########################
    # Message Handling Methods

    def m_ping(self, target, value):
        # Ping a specific subscriber and ask if they are alive
        self.send(target, {'key':'PING', 'value':''})

    def m_init(self, target, value):
        # Ping all pis that we are expecting given our pilot db until we get a response
        # Pass back who responds as they do to update the GUI.
        # We don't expect a response, so we don't send through normal publishing method

        # TODO: Redo this
        pass

        # # Override target if any is fed to us
        # target = b'X' # TODO: Allow all pub/sub key to be defined in setup functions
        #
        # # Publish a general ping five times, m_alive will update our list of subscribers as they respond
        # for i in range(3):
        #     msg_id = str(self.msg_counter.next())
        #     self.publisher.send_multipart([bytes(target), json.dumps({'key':'PING', 'value':'', 'id':msg_id})])
        #     self.logger.info('PUBLISH {} - Target: {}, Key: {}'.format(msg_id, target, 'PING'))
        #     time.sleep(1)
        #
        # # If we still haven't heard from pis that we expected to, we'll ping them a few more times
        # pis = set(value)
        # if not len(pis - self.subscribers) == 0:
        #     for i in range(3):
        #         awol_pis = pis - self.subscribers
        #         for p in awol_pis:
        #             msg_id = str(self.msg_counter.next())
        #             self.publisher.send_multipart([bytes(p), json.dumps({'key': 'PING', 'value': '', 'id':msg_id})])
        #             self.logger.info('PUBLISH {} - Target: {}, Key: {}'.format(msg_id, p, 'PING'))
        #         time.sleep(1)
        #
        #     # If we still haven't heard from them, tell the terminal that
        #     awol_pis = pis - self.subscribers
        #     for p in awol_pis:
        #         self.logger.warning('Requested Pilot {} was not heard from'.format(p))
        #         self.publish(b'T',{'key':'DEAD','value':p})



    def m_start_task(self, target, value):
        # Just publish it
        msg = {'key':'START', 'value':value}
        self.send(target, msg)

        # Then let the plot widget know so that it makes and starts a plot
        self.send(bytes('P_{}'.format(target)), msg)

    def m_change(self, target, value):
        # TODO: Should also handle param changes to GUI objects like ntrials, etc.
        pass


    def m_stop(self, target, value):
        msg = {'key':'STOP', 'value':value}
        self.send(target, msg)
        # and the plot widget
        self.send(bytes('P_{}'.format(target)), msg)

        #TODO: also pop mouse value from data function dict

    def m_stopall(self, target, value):
        pass

    def m_listening(self, target, value):
        self.send('T',{'key':'LISTENING', 'value':''})

    def m_kill(self, target, value):
        self.logger.info('Received kill request')

        # Close sockets
        #self.publisher.close()
        #self.messenger.close()
        #self.listener.close()

        # Kill context
        #self.context.term()

        # Stopping the loop should kill the process, as it's what's holding us in run()
        self.loop.stop()


    def l_data(self, target, value):
        # Send through to terminal
        msg = {'key': 'DATA', 'value':value}
        self.send('T', msg)

        # Send to plot widget, which should be listening to "P_{pilot_name}"
        self.send('P_{}'.format(value['pilot']), msg)


    def l_alive(self, target, value):
        # A pi has told us that it is alive and what its filter is
        self.subscribers[value['pilot']] = value['ip']
        self.logger.info('Received ALIVE from {}, ip: {}'.format(value['pilot'], value['ip']))
        # Tell the terminal
        self.send('T',{'key':'ALIVE','value':value})

    def l_event(self, target, value):
        pass

    def l_state(self, target, value):
        pass

    def l_file(self, target, value):
        # The <target> pi has requested some file <value> from us, let's send it back
        # This assumes the file is small, if this starts crashing we'll have to split the message...
        print('file req received')

        full_path = os.path.join(self.prefs['SOUNDDIR'], value)
        with open(full_path, 'rb') as open_file:
            # encode in base64 so json doesn't complain
            file_contents = base64.b64encode(open_file.read())

        file_message = {'path':value, 'file':file_contents}
        message = {'key':'FILE', 'value':file_message}

        self.send(target, message)

        print('file sending inited')



class Pilot_Networking(multiprocessing.Process):
    # Internal Variables/Objects to the Pilot Networking Object
    ctx          = None    # Context
    loop         = None    # IOLoop
    sub_port     = None    # Subscriber Port to Terminal Publisher
    push_port    = None    # Port to push messages back to Terminal
    message_port = None    # Port to receive messages from the Pilot
    subscriber   = None    # Subscriber Handler - For receiving messages from the terminal
    pusher       = None    # Pusher Handler - For pushing data back to the terminal
    messenger    = None    # Messenger Handler - For receiving messages from the Pilot

    def __init__(self, name, prefs=None):
        super(Pilot_Networking, self).__init__()
        self.name = name

        # Prefs should be passed to us, try to load from default location if not
        if not prefs:
            try:
                with open('/usr/rpilot/prefs.json') as op:
                    self.prefs = json.load(op)
            except:
                logging.exception('No prefs file passed, and none found!')
        else:
            self.prefs = prefs

        self.ip = self.get_ip()

        # Setup logging
        timestr = datetime.datetime.now().strftime('%y%m%d_%H%M%S')
        log_file = os.path.join(self.prefs['LOGDIR'], 'Networking_Log_{}.log'.format(timestr))

        self.logger = logging.getLogger('networking')
        self.log_handler = logging.FileHandler(log_file)
        self.log_formatter = logging.Formatter("%(asctime)s %(levelname)s : %(message)s")
        self.log_handler.setFormatter(self.log_formatter)
        self.logger.addHandler(self.log_handler)
        self.logger.setLevel(logging.INFO)
        self.logger.info('Networking Logging Initiated')

        self.state = None # To respond to queries without bothing the pilot

        self.file_block = threading.Event() # to wait for file transfer

    def run(self):

        # Store some prefs values
        self.name = self.prefs['NAME']
        self.sub_port = self.prefs['SUBPORT']
        self.push_port = self.prefs['PUSHPORT']
        self.message_in_port = self.prefs['MSGINPORT']
        self.message_out_port = self.prefs['MSGOUTPORT']
        self.terminal_ip = self.prefs['TERMINALIP']
        self.mouse = None # To store mouse name

        # Initialize Network Objects
        self.context = zmq.Context()

        self.loop = IOLoop.instance()

        # Instantiate and connect sockets
        # Subscriber listens for publishes from terminal networking
        # Pusher pushes data/messages back
        # Messenger receives messages from Pilot parent
        self.subscriber = self.context.socket(zmq.SUB)
        self.pusher     = self.context.socket(zmq.PUSH)
        self.message_in  = self.context.socket(zmq.PULL)
        self.message_out = self.context.socket(zmq.PUSH)

        self.subscriber.connect('tcp://{}:{}'.format(self.terminal_ip, self.sub_port))
        self.pusher.connect('tcp://{}:{}'.format(self.terminal_ip, self.push_port))
        self.message_in.bind('tcp://*:{}'.format(self.message_in_port))
        self.message_out.bind('tcp://*:{}'.format(self.message_out_port))

        # Set subscriber filters
        self.subscriber.setsockopt(zmq.SUBSCRIBE, bytes(self.name))
        self.subscriber.setsockopt(zmq.SUBSCRIBE, b'X')

        # Wrap as ZMQStreams
        self.subscriber = ZMQStream(self.subscriber, self.loop)
        self.message_in  = ZMQStream(self.message_in, self.loop)

        # Set on_recv callbacks
        self.subscriber.on_recv(self.handle_listen)
        self.message_in.on_recv(self.handle_message)

        # Message dictionary - What method to call for messages from the parent Pilot
        self.messages = {
            'STATE': self.m_state, # Confirm or notify terminal of state change
            'DATA': self.m_data,  # Sending data back
            'COHERE': self.m_cohere, # Sending our temporary data table at the end of a run to compare w/ terminal's copy
            'ALIVE': self.m_alive # send some initial information to the terminal
        }

        # Listen dictionary - What method to call for PUBlishes from the Terminal
        self.listens = {
            'PING': self.l_ping, # The Terminal wants to know if we're listening
            'START': self.l_start, # We are being sent a task to start
            'STOP': self.l_stop, # We are being told to stop the current task
            'PARAM': self.l_change, # The Terminal is changing some task parameter
            'FILE':  self.l_file, # We are receiving a file
        }

        self.logger.info('Starting IOLoop')
        self.loop.start()

    def push(self, key, target='', value=''):
        # Push a message back to the terminal
        msg = {'key': key, 'target': target, 'value': value}

        push_thread = threading.Thread(target=self.pusher.send_json, args=(json.dumps(msg),))
        push_thread.start()

        self.logger.info("PUSH SENT - Target: {}, Key: {}, Value: {}".format(target, key, value))

    def handle_listen(self, msg, suppress_print=False):
        # listens are always json encoded, single-part messages
        msg = json.loads(msg[1])

        # Check if our listen was sent properly
        if not all(i in msg.keys() for i in ['key','value']):
            logging.warning('LISTEN Improperly formatted: {}'.format(msg))
            return

        if msg['key'] == 'FILE':
            suppress_print = True

        if not suppress_print:
            self.logger.info('LISTEN {} - KEY: {}, VALUE: {}'.format(msg['id'], msg['key'], msg['value']))
        else:
            self.logger.info('LISTEN {} - KEY: {}'.format(msg['id'], msg['key']))

        # Log and spawn thread to respond to listen
        listen_funk = self.listens[msg['key']]
        listen_thread = threading.Thread(target=listen_funk, args=(msg['value'],))
        listen_thread.start()

        # Then let the terminal know we got the message
        self.push('RECVD', value=msg['id'])

    def handle_message(self, msg):
        # Messages are always json encoded, single-part messages
        msg = json.loads(msg[0])
        if isinstance(msg, unicode or basestring):
            msg = json.loads(msg)

        # Check if message formatted properly
        if not all(i in msg.keys() for i in ['key', 'target', 'value']):
            self.logger.warning("MESSAGE IN Improperly formatted: {}".format(msg))
            return

        # Log message
        self.logger.info('MESSAGE IN - KEY: {}, TARGET: {}, VALUE: {}'.format(msg['key'], msg['target'], msg['value']))

        # Spawn thread to handle message
        message_funk = self.messages[msg['key']]
        message_thread = threading.Thread(target=message_funk, args=[msg['target'], msg['value']])
        message_thread.start()

    def send_message_out(self, key, value=''):
        # send a message out to the pi
        msg = {'key':key, 'value':value}

        msg_thread = threading.Thread(target = self.message_out.send_json, args=(json.dumps(msg),))
        msg_thread.start()

        self.logger.info("MESSAGE OUT SENT - Key: {}, Value: {}".format(key, value))

    def get_ip(self):
        # shamelessly stolen from https://www.w3resource.com/python-exercises/python-basic-exercise-55.php
        # variables are badly named because this is just a rough unwrapping of what was a monstrous one-liner

        # get ips that aren't the loopback
        unwrap00 = [ip for ip in socket.gethostbyname_ex(socket.gethostname())[2] if not ip.startswith("127.")][:1]
        # ???
        unwrap01 = [[(s.connect(('8.8.8.8', 53)), s.getsockname()[0], s.close()) for s in
                     [socket.socket(socket.AF_INET, socket.SOCK_DGRAM)]][0][1]]

        unwrap2 = [l for l in (unwrap00, unwrap01) if l][0][0]

        return unwrap2

    ###########################3
    # Message/Listen handling methods
    def m_state(self, target, value):
        # Save locally so we can respond to queries on our own, then push 'er on through
        # Value will just have the state, we want to add our name
        self.state = value
        state_message = {'name': self.name, 'state': value}
        self.push('STATE', target, state_message)

    def m_data(self, target, value):
        # Just sending it along after appending the mouse name
        value['mouse'] = self.mouse
        value['pilot'] = self.name
        self.push('DATA', target, value)

    def m_cohere(self, target, value):
        # Send our local version of the data table so the terminal can double check
        pass

    def m_alive(self, target, value):
        # just say hello
        self.push('ALIVE', target=target, value=value)

    def l_ping(self, value):
        # The terminal wants to know if we are alive, respond with our name and IP
        self.push('ALIVE', value={'pilot':self.name, 'ip':self.ip})

    def l_start(self, value):
        self.mouse = value['mouse']

        # First make sure we have any sound files that we need
        # nested list comprehension to get value['sounds']['L/R'][0-n]
        if 'sounds' in value.keys():
            f_sounds = [sound for sounds in value['sounds'].values() for sound in sounds
                        if sound['type'] == 'File']
            if len(f_sounds)>0:
                for sound in f_sounds:
                    full_path = os.path.join(self.prefs['SOUNDDIR'], sound['path'])
                    if not os.path.exists(full_path):
                        # We ask the terminal to send us the file and then wait.
                        self.logger.info('REQUESTING SOUND {}'.format(sound['path']))
                        self.push(key='FILE', target=self.name, value=sound['path'])
                        self.file_block.clear()
                        self.file_block.wait()


        self.send_message_out('START', value)

    def l_stop(self, value):
        self.send_message_out('STOP')

    def l_change(self, value):
        pass

    def l_file(self, value):
        # The file should be of the structure {'path':path, 'file':contents}

        full_path = os.path.join(self.prefs['SOUNDDIR'], value['path'])
        file_data = base64.b64decode(value['file'])
        with open(full_path, 'wb') as open_file:
            open_file.write(file_data)

        self.logger.info('SOUND RECEIVED {}'.format(value['path']))

        # If we requested a file, some poor start fn is probably waiting on us
        self.file_block.set()






































