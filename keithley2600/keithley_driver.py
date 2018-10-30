# -*- coding: utf-8 -*-
#
# Copyright © keithley2600 Project Contributors
# Licensed under the terms of the MIT License
# (see keithley2600/__init__.py for details)

"""
Core driver with the low level functions

"""

# system imports
import visa
import logging
import threading
import numpy as np
import time

# local import
from keithley2600.keithley_doc import CONSTANTS, FUNCTIONS, PROPERTIES, CLASSES, PROPERTY_LISTS
from keithley2600.sweep_data_class import TransistorSweepData

logger = logging.getLogger(__name__)


class MagicPropertyList(object):
    """

    Class which mimics a property and can be dynamically created. It fowards
    all calls to the _read method of the parent class and assignments to the
    _write method. Aribitrary values can be assigned, as long as _write can
    handle them.

    This class is designed to look like a  Keithley TSP "attribute", forward
    function calls to the Keithley, and return the results.

    """

    def __init__(self, name, parent):
        if type(name) is not str:
            raise ValueError('First argument must be of type str.')
        self._name = name
        self._parent = parent

    def __getitem__(self, i):
        new_name = '%s[%s]' % (self._name, i)
        return self._query(new_name)

    def __setitem__(self, i, value):
        new_name = '%s[%s] = %s' % (self._name, i, value)
        return self._write(new_name)

    def __iter__(self):
        return self

    def _write(self, value):
        self._parent._write(value)

    def _query(self, value):
        return self._parent._query(value)

    def _convert_input(self, value):
        try:
            return self._parent._convert_input(value)
        except AttributeError:
            return value

    def getdoc():
        pass


class MagicFunction(object):
    """

    Class which mimics a function and can be dynamically created. It fowards
    all calls to the _query method of the parent class and returns the result
    from _query. Calls accept aribitrary arguments, as long as _query can
    handle them.

    This class is designed to look like a  Keithley TSP function, forward
    function calls to the Keithley, and return the results.

    """

    def __init__(self, name, parent):
        if type(name) is not str:
            raise ValueError('First argument must be of type str.')
        self._name = name
        self._parent = parent

    def __call__(self, *args, **kwargs):
        # Pass on to calls to self._write, store result in variable.
        # Querying results from function calls directly may result in
        # a VisaIOError timeout if the function does not return anything.
        args_string = str(args).strip("(),").replace("'", "")
        self._parent._write('result = %s(%s)' % (self._name, args_string))
        # query for result in second call
        return self._parent._query('result')


class MagicClass(object):
    """

    Class which dynamically creates new attributes on access. These can be
    functions, properties, or other classes.

    MagicClass need the strings in FUNCTIONS and PROPERTIES to determine if the
    accessed attribute should behave like a function or property. Otherwise, it
    is assumed to be a new class.

    Attribute setters and getters are forwarded to _write and _query functions
    from the parent class. New functions are created as instances of
    MagicFunction, new classes are created as instances of MagicClass.

    MagicClass is designed to mimic a Keithley TSP command group with
    functions, attributes, and subordinate command groups.

    USAGE:
        inst = MagicClass('keithley')
        inst.reset() - Dynamically creates a new attribute 'reset' as an instance
                       of MagicFunction, then calls it.
        inst.beeper  - Dynamically creats new attribute 'beeper' and sets it to
                       a new MagicClass instance.
        newclass.beeper.enable - Fakes the property 'enable' of 'beeper'
                                 with _write as setter and _query as getter.

    """

    _name = ''
    _parent = None

    def __init__(self, name, parent=None):
        if type(name) is not str:
            raise ValueError('First argument must be of type str.')
        self._name = name
        self._parent = parent

    def __getattr__(self, attr):
        try:
            try:
                # check if attribute already exists. return attr if yes.
                return object.__getattr__(self, attr)
            except AttributeError:
                # check if key already exists. return value if yes.
                return self.__dict__[attr]
        except KeyError:
            # handle if not
            return self.__get_global_handler(attr)

    def __get_global_handler(self, name):
        # create callable sub-class for new attr
        new_name = '%s.%s' % (self._name, name)
        new_name = new_name.strip('.')

        if name in FUNCTIONS:
            handler = MagicFunction(new_name, parent=self)
            self.__dict__[new_name] = handler

        elif name in PROPERTIES or name in CONSTANTS:
            if new_name in PROPERTY_LISTS:
                handler = MagicPropertyList(new_name, parent=self)
            else:
                handler = self._query(new_name)

        elif name in CLASSES:
            handler = MagicClass(new_name, parent=self)
            self.__dict__[new_name] = handler

        else:
            raise AttributeError("'%s' object has no attribute '%s'" % (type(self), name))

        return handler

    def __setattr__(self, attr, value):
        if attr in PROPERTIES:
            value = self._convert_input(value)
            self._write('%s.%s = %s' % (self._name, attr, value))
        elif attr in CONSTANTS:
            raise ValueError('%s.%s is read-only.' % (self._name, attr))
        else:
            object.__setattr__(self, attr, value)
            self.__dict__[attr] = value

    def _write(self, value):
        self._parent._write(value)

    def _query(self, value):
        return self._parent._query(value)

    def _convert_input(self, value):
        try:
            return self._parent._convert_input(value)
        except AttributeError:
            return value

    def __getitem__(self, i):
        new_name = '%s[%s]' % (self._name, i)
        new_class = MagicClass(new_name, parent=self)
        return new_class

    def __iter__(self):
        return self

    def getdoc():
        pass


class Keithley2600Base(MagicClass):
    """

    Keithley driver for base functions. It replicates the functionality and
    syntax from the Keithley TSP commands, which have a syntax similar to
    python.

    WARNING:
        There are currntly no checks for allowed arguments in the base
        commands. See the Keithley 2600 reference manual for all available
        commands and arguments. Almost all remotely accessible commands can be
        used with this driver. NOT SUPPORTED ARE:
             * tspnet.excecute() # conflicts with Python's excecute command
             * lan.trigger[N].connected # conflicts with connected attribute of Keithley2600Base
             * All Keithley IV sweep commands. We implement our own in the
               Keithley2600 class.

    USAGE:
        >>> keithley = Keithley2600Base('TCPIP0::192.168.2.121::INSTR')
        >>> keithley.smua.measure.v()  # measures the smuA voltage
        >>> keithley.smua.source.levelv = -40  # applies -40V to smuA

    DOCUMENTATION:
        See the Keithley 2600 reference manual for all available commands and
        arguments.

    """

    _lock = threading.RLock()
    abort_event = threading.Event()

    connection = None
    connected = False
    busy = False

# =============================================================================
# Connect to keithley
# =============================================================================

    def __init__(self, visa_address, visa_library):
        MagicClass.__init__(self, name='', parent=self)

        self.visa_address = visa_address
        self.visa_library = visa_library

        # open visa resource manager with selected library / backend
        self.rm = visa.ResourceManager(self.visa_library)
        # connect to keithley
        self.connect()

    def connect(self):
        """
        Connects to Keithley and opens pyvisa API.
        """
        self.connection = self.rm.open_resource(self.visa_address)
        try:
            self.connection = self.rm.open_resource(self.visa_address)
            self.connection.read_termination = '\n'
            self.connected = True

            self.beeper.beep(0.3, 1046.5)
            self.beeper.beep(0.3, 1318.5)
            self.beeper.beep(0.3, 1568)
        except:
            # TODO: catch specific errors once implemented in pyvisa-py
            logger.warning('Could not connect to Keithley.')
            self.connection = None
            self.connected = False

    def disconnect(self):
        """ Disconnect from Keithley """
        if self.connection:
            try:
                self.beeper.beep(0.3, 1568)
                self.beeper.beep(0.3, 1318.5)
                self.beeper.beep(0.3, 1046.5)

                self.connection.close()
                self.connection = None
                self.connected = False
                del self.connection
            except AttributeError:
                self.connected = False
                pass

# =============================================================================
# Define I/O
# =============================================================================

    def _write(self, value):
        """
        Writes text to Keithley. Input must be a string.
        """
        logger.debug(value)

        if self.connection:
            self.connection.write(value)
        else:
            raise OSError('No keithley connected.')

    def _query(self, value):
        """
        Queries and expects response from Keithley. Input must be a string.
        """
        logger.debug('print(%s)' % value)

        if self.connection:
            with self._lock:
                r = self.connection.query('print(%s)' % value)

            return self.parse_response(r)
        else:
            raise OSError('No keithley connected.')

    def parse_response(self, string):
        try:
            r = float(string)
        except ValueError:
            if string == 'nil':
                r = None
            elif string == 'true':
                r = True
            elif string == 'false':
                r = False
            else:
                r = string

        return r

    def _convert_input(self, value):
        if isinstance(value, bool):
            value = str(value).lower()

        return value


class Keithley2600(Keithley2600Base):
    """

    Keithley driver with acccess to base functions and higher level functions
    such as IV measurements, tranfer and output curves, etc. Base command
    replicate the functionality and syntax from the Keithley TSP functions,
    which have a syntax similar to python.

    WARNING:
        There are currntly no checks for allowed arguments in the base
        commands. See the Keithley 2600 reference manual for all available
        commands and arguments. Almost all remotely accessible commands can be
        used with this driver. NOT SUPPORTED ARE:
             * tspnet.excecute() # conflicts with Python's excecute command
             * All Keithley IV sweep commands. We implement our own here.

    EXAMPLE USAGE:
        Base commands from keithley TSP:

        >>> k = Keithley2600('TCPIP0::192.168.2.121::INSTR')
        >>> k.smua.measure.v()  # measures the smuA voltage
        >>> k.smua.source.levelv = -40  # sets source level of smuA

        New mid-level commands:

        >>> data = k.readBuffer('smua.nvbuffer1')
        >>> k.clearBuffers() # clears ALL smu buffers
        >>> k.setIntegrationTime(k.smua, 0.001) # in sec

        >>> k.applyVoltage(k.smua, -60) # applies -60V to smuA
        >>> k.applyCurrent(k.smub, 0.1) # sources 0.1A from smuB
        >>> k.rampToVoltage(k.smua, 10, delay=0.1, stepSize=1)

        >>> k.voltageSweep(smu_sweep=k.smua, smu_fix=k.smub, VStart=0,
                           VStop=-60, VStep=1, VFix=0, tInt=0.1, delay=-1,
                           pulsed=True) # records IV curve

        New high-level commands:

        >>> data1 = k.outputMeasurement(...) # records output curve
        >>> data2 = k.transferMeasurement(...) # records transfer curve

    """

    SMU_LIST = ['smua', 'smub']

    def __init__(self, visa_address, visa_library=''):
        Keithley2600Base.__init__(self, visa_address, visa_library)

    def _check_smu(self, smu):
        """Check if selected smu is indeed present."""
        assert smu._name.split('.')[-1] in self.SMU_LIST

    def _get_smu_string(self, smu):
        return smu._name.split('.')[-1]

# =============================================================================
# Define lower level control functions
# =============================================================================

    def readBuffer(self, bufferName):
        """
        Reads buffer values and returns them as a list.
        Clears buffer afterwards.
        """
        n = int(float(self._query('%s.n' % bufferName)))
        list_out = [0.00] * n
        for i in range(0, n):
            list_out[i] = float(self._query('%s[%d]' % (bufferName, i+1)))

        # clears buffer
        self._write('%s.clear()' % bufferName)
        self._write('%s.clearcache()' % bufferName)
        return list_out

    def clearBuffers(self):
        """ Clears all SMU buffers."""
        for smu_string in self.SMU_LIST:
            smu = getattr(self, smu_string)

            smu.nvbuffer1.clear()
            smu.nvbuffer2.clear()

            smu.nvbuffer1.clearcache()
            smu.nvbuffer2.clearcache()

    def setIntegrationTime(self, smu, tInt):
        """ Sets the integration time of SMU for measurements in sec. """

        self._check_smu(smu)

        # determine number of power-line-cycles used for integration
        nplc = tInt * self.localnode.linefreq
        smu.measure.nplc = nplc

    def applyVoltage(self, smu, voltage):
        """
        Turns on the specified SMU and applies a voltage.
        """

        self._check_smu(smu)

        smu.source.output = smu.OUTPUT_ON
        smu.source.levelv = voltage

    def applyCurrent(self, smu, curr):
        """
        Turns on the specified SMU and sources a current.
        """
        self._check_smu(smu)

        smu.source.leveli = curr
        smu.source.output = smu.OUTPUT_ON

    def rampToVoltage(self, smu, targetVolt, delay=0.1, stepSize=1):
        """
        Ramps up the voltage of the specified SMU. Beeps when done.

        INPUT:
            targetVolt - target gate voltage
            stepSize - size of voltage ramp steps in Volts
            delay -  delay between steps in sec
        """

        self._check_smu(smu)

        smu.source.output = smu.OUTPUT_ON

        # get current voltage
        Vcurr = smu.source.levelv
        if Vcurr == targetVolt:
            return

        self.display.smua.measure.func = self.display.MEASURE_DCVOLTS
        self.display.smub.measure.func = self.display.MEASURE_DCVOLTS

        step = np.sign(targetVolt-Vcurr)*abs(stepSize)

        for V in np.arange(Vcurr-step, targetVolt-step, step):
            smu.source.levelv = V
            smu.measure.v()
            time.sleep(delay)

        targetVolt = smu.measure.v()
        logger.info('Gate voltage set to Vg = %s V.' % round(targetVolt))

        self.beeper.beep(0.3, 2400)

    def voltageSweep(self, smu_sweep, smu_fix, VStart, VStop, VStep, VFix,
                     tInt, delay, pulsed):
        """
        Sweeps voltage at one SMU (smu_sweep) while keeping the second at
        a constant voltage VFix. The option VFix = 'trailing' will sweep both
        SMUs simulateously.

        Measures and returns current and voltage during sweep.

        INPUTS:
            smu_sweep - SMU to be sweept
            smu_fix - SMU to be kept at fixed voltage
            VStart - start voltage for sweep (float)
            Vstop - stop voltage for sweep (float)
            VStep - sweep steps (float)
            VFix - constant voltage for second SMU (Volts or 'trailing')
            tInt - integration time per data point (float)
            delay - settling delay before measurement (float)
            pulsed - continous or pulsed sweep (bool)
        """
        self._check_smu(smu_sweep)
        self._check_smu(smu_fix)

        self.busy = True
        # define list containing results. If we abort early, we have something
        # to return
        Vsweep, Isweep, Vfix, Ifix = [], [], [], []

        if self.abort_event.is_set():
            self.busy = False
            return Vsweep, Isweep, Vfix, Ifix

        # Setup smua/smub for sweep measurement. The voltage is swept from
        # VStart to VStop in intervals of VStep with a measuremnt at each step.
        numPoints = 1 + abs((VStop-VStart)/VStep)

        # setup smu_sweep to sweep votage linearly
        smu_sweep.trigger.source.linearv(VStart, VStop, numPoints)
        smu_sweep.trigger.source.action = smu_sweep.ENABLE

        if VFix == 'trailing':
            # setup smu_fix to sweep votage linearly
            smu_fix.trigger.source.linearv(VStart, VStop, numPoints)
            smu_fix.trigger.source.action = smu_fix.ENABLE

        else:
            # setup smu_fix to remain at a constant voltage
            smu_fix.trigger.source.linearv(VFix, VFix, numPoints)
            smu_fix.trigger.source.action = smu_fix.ENABLE

        # CONFIGURE INTEGRATION TIME FOR EACH MEASUREMENT
        nplc = tInt * self.localnode.linefreq
        smu_sweep.measure.nplc = nplc
        smu_fix.measure.nplc = nplc

        # CONFIGURE SETTLING TIME FOR GATE VOLTAGE, I-LIMIT, ETC...
        smu_sweep.measure.delay = delay
        smu_fix.measure.delay = delay

        smu_sweep.measure.autorangei = smu_sweep.AUTORANGE_ON
        smu_fix.measure.autorangei = smu_fix.AUTORANGE_ON

        # smu_sweep.trigger.source.limiti = 0.1
        # smu_fix.trigger.source.limiti = 0.1

        smu_sweep.source.func = smu_sweep.OUTPUT_DCVOLTS
        smu_fix.source.func = smu_fix.OUTPUT_DCVOLTS

        # 2-wire measurement (use SENSE_REMOTE for 4-wire)
        # smu_sweep.sense = smu_sweep.SENSE_LOCAL
        # smu_fix.sense = smu_fix.SENSE_LOCAL

        # clears SMU buffers
        smu_sweep.nvbuffer1.clear()
        smu_sweep.nvbuffer2.clear()
        smu_fix.nvbuffer1.clear()
        smu_fix.nvbuffer2.clear()

        smu_sweep.nvbuffer1.clearcache()
        smu_sweep.nvbuffer2.clearcache()
        smu_fix.nvbuffer1.clearcache()
        smu_fix.nvbuffer2.clearcache()

        # diplay current values during measurement
        self.display.smua.measure.func = self.display.MEASURE_DCAMPS
        self.display.smub.measure.func = self.display.MEASURE_DCAMPS

        # SETUP TRIGGER ARM AND COUNTS
        # trigger count = number of data points in measurement
        # arm count = number of times the measurement is repeated (set to 1)

        smu_sweep.trigger.count = numPoints
        smu_fix.trigger.count = numPoints

        # SET THE MEASUREMENT TRIGGER ON BOTH SMU'S
        # Set measurment to trigger once a change in the gate value on
        # sweep smu is complete, i.e., a measurment will occur
        # after the voltage is stepped.
        # Both channels should be set to trigger on the sweep smu event
        # so the measurements occur at the same time.

        # enable smu
        smu_sweep.trigger.measure.action = smu_sweep.ENABLE
        smu_fix.trigger.measure.action = smu_fix.ENABLE
        # measure current on trigger, store in buffer of smu

        buffer_sweep_1 = '%s.nvbuffer1' % self._get_smu_string(smu_sweep)
        buffer_sweep_2 = '%s.nvbuffer2' % self._get_smu_string(smu_sweep)

        buffer_fix_1 = '%s.nvbuffer1' % self._get_smu_string(smu_fix)
        buffer_fix_2 = '%s.nvbuffer2' % self._get_smu_string(smu_fix)

        smu_sweep.trigger.measure.iv(buffer_sweep_1, buffer_sweep_2)
        smu_fix.trigger.measure.iv(buffer_fix_1, buffer_fix_2)
        # initiate measure trigger when source is complete
        smu_sweep.trigger.measure.stimulus = smu_sweep.trigger.SOURCE_COMPLETE_EVENT_ID
        smu_fix.trigger.measure.stimulus = smu_sweep.trigger.SOURCE_COMPLETE_EVENT_ID

        # SET THE ENDPULSE ACTION TO HOLD
        # Options are SOURCE_HOLD AND SOURCE_IDLE, hold maintains same voltage
        # throughout step in sweep (typical IV sweep behavior). idle will allow
        # pulsed IV sweeps.

        if pulsed:
            endPulseAction = 0  # SOURCE_IDLE
        elif not pulsed:
            endPulseAction = 1  # SOURCE_HOLD
        else:
            raise TypeError("'pulsed' must be of type 'bool'.")

        smu_sweep.trigger.endpulse.action = endPulseAction
        smu_fix.trigger.endpulse.action = endPulseAction

        # SET THE ENDSWEEP ACTION TO HOLD IF NOT PULSED
        # Output voltage will be held after sweep is done!

        smu_sweep.trigger.endsweep.action = endPulseAction
        smu_fix.trigger.endsweep.action = endPulseAction

        # SET THE EVENT TO TRIGGER THE SMU'S TO THE ARM LAYER
        # A typical measurement goes from idle -> arm -> trigger.
        # The 'trigger.event_id' option sets the transition arm -> trigger
        # to occur after sending *trg to the instrument.

        smu_sweep.trigger.arm.stimulus = self.trigger.EVENT_ID

        # Prepare an event blender (blender #1) that triggers when
        # the smua enters the trigger layer or reaches the end of a
        # single trigger layer cycle.

        # triggers when either of the stimuli are true ('or enable')
        self.trigger.blender[1].orenable = True
        self.trigger.blender[1].stimulus[1] = smu_sweep.trigger.ARMED_EVENT_ID
        self.trigger.blender[1].stimulus[2] = smu_sweep.trigger.PULSE_COMPLETE_EVENT_ID

        # SET THE smu_sweep SOURCE STIMULUS TO BE EVENT BLENDER #1
        # A source measure cycle within the trigger layer will occur when
        # either the trigger layer is entered (termed 'armed event') for the
        # first time or a single cycle of the trigger layer is complete (termed
        # 'pulse complete event').

        smu_sweep.trigger.source.stimulus = self.trigger.blender[1].EVENT_ID

        # PREPARE AN EVENT BLENDER (blender #2) THAT TRIGGERS WHEN BOTH SMU'S
        # HAVE COMPLETED A MEASUREMENT.
        # This is needed to prevent the next source measure cycle from occuring
        # before the measurement on both channels is complete.

        self.trigger.blender[2].orenable = False  # triggers when both stimuli are true
        self.trigger.blender[2].stimulus[1] = smu_sweep.trigger.MEASURE_COMPLETE_EVENT_ID
        self.trigger.blender[2].stimulus[2] = smu_fix.trigger.MEASURE_COMPLETE_EVENT_ID

        # SET THE smu_sweep ENDPULSE STIMULUS TO BE EVENT BLENDER #2
        smu_sweep.trigger.endpulse.stimulus = self.trigger.blender[2].EVENT_ID

        # TURN ON smu_sweep AND smu_fix
        smu_sweep.source.output = smu_sweep.OUTPUT_ON
        smu_fix.source.output = smu_fix.OUTPUT_ON

        # INITIATE MEASUREMENT
        # prepare SMUs to wait for trigger
        smu_sweep.trigger.initiate()
        smu_fix.trigger.initiate()
        # send trigger
        self._write('*trg')

        # CHECK STATUS BUFFER FOR MEASUREMENT TO FINISH
        # Possible return values:
        # 6 = smua and smub sweeping
        # 4 = only smub sweeping
        # 2 = only smua sweeping
        # 0 = neither smu sweeping

        status = 0
        while status == 0:  # while loop that runs until the sweep begins
            status = self.status.operation.sweeping.condition

        while status > 0:  # while loop that runs until the sweep ends
            status = self.status.operation.sweeping.condition

        # EXTRACT DATA FROM SMU BUFFERS

        Vsweep = self.readBuffer(buffer_sweep_2)
        Isweep = self.readBuffer(buffer_sweep_1)
        Vfix = self.readBuffer(buffer_fix_2)
        Ifix = self.readBuffer(buffer_fix_1)

        self.clearBuffers()
        self.busy = False

        return Vsweep, Isweep, Vfix, Ifix

# =============================================================================
# Define higher level control functions
# =============================================================================

    def transferMeasurement(self, smu_gate, smu_drain, VgStart, VgStop, VgStep,
                            VdList, tInt, delay, pulsed):

        """
        Records a transfer curve and saves the results in a TransistorSweepData
        instance.
        """
        self.busy = True
        self.abort_event.clear()
        msg = ('Recording transfer curve with Vg from %sV to %sV, Vd = %s V. '
               % (VgStart, VgStop, VdList))
        logger.info(msg)

        # create TransistorSweepData instance
        sd = TransistorSweepData(sweepType='transfer')

        for Vdrain in VdList:
            if self.abort_event.is_set():
                self.reset()
                self.beeper.beep(0.3, 2400)
                return sd

            # conduct forward and reverse sweeps
            VsFWD, IsFWD, VfFWD, IfFWD = self.voltageSweep(smu_gate, smu_drain,
                                                           VgStart, VgStop,
                                                           -abs(VgStep),
                                                           Vdrain, tInt, delay,
                                                           pulsed)

            if not self.abort_event.is_set():
                # add data to TransistorSweepData instance
                # discard data if aborted by user
                sd.append(vFix=Vdrain, vSweep=VsFWD, iDrain=IfFWD, iGate=IsFWD)

            VsRVS, IsRVS, VfRVS, IfRVS = self.voltageSweep(smu_gate, smu_drain,
                                                           VgStop, VgStart,
                                                           abs(VgStep), Vdrain,
                                                           tInt, delay, pulsed)

            if not self.abort_event.is_set():
                # add data to TransistorSweepData instance
                # discard data if aborted by user
                sd.append(vFix=Vdrain, vSweep=VsRVS, iDrain=IfRVS, iGate=IsRVS)

        self.reset()
        self.beeper.beep(0.3, 2400)

        self.busy = False
        return sd

    def outputMeasurement(self, smu_gate, smu_drain, VdStart, VdStop, VdStep,
                          VgList, tInt, delay, pulsed):
        """
        Records a output curve and saves the results in a TransistorSweepData
        instance.
        """
        self.busy = True
        self.abort_event.clear()
        msg = ('Recording output curve with Vd from %sV to %sV, Vg = %s V. '
               % (VdStart, VdStop, VgList))
        logger.info(msg)

        # create TransistorSweepData instance
        sd = TransistorSweepData(sweepType='output')

        for Vgate in VgList:
            if self.abort_event.is_set():
                self.reset()
                self.beeper.beep(0.3, 2400)
                return sd

            # conduct forward and reverse sweeps
            VsFWD, IsFWD, VfFWD, IfFWD = self.voltageSweep(smu_drain, smu_gate,
                                                           VdStart, VdStop,
                                                           -abs(VdStep), Vgate,
                                                           tInt, delay, pulsed)
            if not self.abort_event.is_set():
                # add data to TransistorSweepData instance
                # discard data if aborted by user
                sd.append(vFix=Vgate, vSweep=VsFWD, iDrain=IsFWD, iGate=IfFWD)

            VsRVS, IsRVS, VfRVS, IfRVS = self.voltageSweep(smu_drain, smu_gate,
                                                           VdStop, VdStart,
                                                           abs(VdStep), Vgate,
                                                           tInt, delay, pulsed)

            if not self.abort_event.is_set():
                # add data to TransistorSweepData instance
                # discard data if aborted by user
                sd.append(vFix=Vgate, vSweep=VsRVS, iDrain=IsRVS, iGate=IfRVS)

        self.reset()
        self.beeper.beep(0.3, 2400)

        self.busy = False
        return sd

    def playChord(self, direction='up'):

        if direction is 'up':
            self.beeper.beep(0.3, 1046.5)
            self.beeper.beep(0.3, 1318.5)
            self.beeper.beep(0.3, 1568)

        elif direction is 'down':
            self.beeper.beep(0.3, 1568)
            self.beeper.beep(0.3, 1318.5)
            self.beeper.beep(0.3, 1046.5)
        else:
            self.beeper.beep(0.2, 1046.5)
            self.beeper.beep(0.1, 1046.5)
