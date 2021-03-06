# -*- coding: utf-8 -*-
"""
Module for reading/writing data from/to legacy PyNN formats.

PyNN is available at http://neuralensemble.org/PyNN

Classes:
    PyNNNumpyIO
    PyNNTextIO

Supported: Read/Write

Authors: Andrew Davison, Pierre Yger
"""

from itertools import chain
import numpy
import quantities as pq
import warnings

from neo.io.baseio import BaseIO
from neo.core import Segment, AnalogSignal, SpikeTrain

try:
    unicode
    PY2 = True
except NameError:
    PY2 = False

UNITS_MAP = {
    'spikes': pq.ms,
    'v': pq.mV,
    'gsyn': pq.UnitQuantity('microsiemens', 1e-6 * pq.S, 'uS', 'µS'),  # checked
}


class BasePyNNIO(BaseIO):
    """
    Base class for PyNN IO classes
    """
    is_readable = True
    is_writable = True
    has_header = True
    is_streameable = False  # TODO - correct spelling to "is_streamable"
    supported_objects = [Segment, AnalogSignal, SpikeTrain]
    readable_objects = supported_objects
    writeable_objects = supported_objects
    mode = 'file'

    def __init__(self, filename=None, **kargs):
        BaseIO.__init__(self, filename, *kargs)
        warnings.warn("PyNNTextIO and PyNNNumpyIO will be removed in Neo 0.7.0. " +
                      "Please contact the Neo developers if this will cause you problems.",
                      DeprecationWarning)

    def _read_file_contents(self):
        raise NotImplementedError

    def _extract_array(self, data, channel_index):
        idx = numpy.where(data[:, 1] == channel_index)[0]
        return data[idx, 0]

    def _determine_units(self, metadata):
        if 'units' in metadata:
            return metadata['units']
        elif 'variable' in metadata and metadata['variable'] in UNITS_MAP:
            return UNITS_MAP[metadata['variable']]
        else:
            raise IOError("Cannot determine units")

    def _extract_signals(self, data, metadata):

        arr = numpy.vstack(self._extract_array(data, channel_index)
                           for channel_index in
                           range(metadata['first_index'], metadata['last_index'] + 1))
        if len(arr) > 0:
            signal = AnalogSignal(arr.T,
                                  units=self._determine_units(metadata),
                                  sampling_period=metadata['dt'] * pq.ms)
        signal.annotate(label=metadata["label"],
                        variable=metadata["variable"])
        return signal

    def _extract_spikes(self, data, metadata, channel_index):
        spiketrain = None
        spike_times = self._extract_array(data, channel_index)
        if len(spike_times) > 0:
            spiketrain = SpikeTrain(spike_times, units=pq.ms, t_stop=spike_times.max())
            spiketrain.annotate(label=metadata["label"],
                                channel_index=channel_index,
                                dt=metadata["dt"])
            return spiketrain

    def _write_file_contents(self, data, metadata):
        raise NotImplementedError

    def read_segment(self, lazy=False):
        assert not lazy, 'Do not support lazy'

        data, metadata = self._read_file_contents()
        annotations = dict((k, metadata.get(k, 'unknown'))
                           for k in ("label", "variable", "first_id", "last_id"))
        seg = Segment(**annotations)
        if metadata['variable'] == 'spikes':
            for i in range(metadata['first_index'], metadata['last_index'] + 1):
                spiketrain = self._extract_spikes(data, metadata, i)
                if spiketrain is not None:
                    seg.spiketrains.append(spiketrain)
            # store dt for SpikeTrains only, as can be retrieved from sampling_period for AnalogSignal
            seg.annotate(dt=metadata['dt'])
        else:
            signal = self._extract_signals(data, metadata)
            if signal is not None:
                seg.analogsignals.append(signal)
        seg.create_many_to_one_relationship()
        return seg

    def write_segment(self, segment):
        source = segment.analogsignals or segment.spiketrains
        assert len(source) > 0, "Segment contains neither analog signals nor spike trains."
        metadata = segment.annotations.copy()
        s0 = source[0]
        if isinstance(s0, AnalogSignal):
            if len(source) > 1:
                warnings.warn("Cannot handle multiple analog signals. Writing only the first.")
            source = s0.T
            metadata['size'] = s0.shape[1]
            n = source.size
        else:
            metadata['size'] = len(source)
            n = sum(s.size for s in source)
        metadata['first_index'] = 0
        metadata['last_index'] = metadata['size'] - 1
        if 'label' not in metadata:
            metadata['label'] = 'unknown'
        if 'dt' not in metadata:  # dt not included in annotations if Segment contains only AnalogSignals
            metadata['dt'] = s0.sampling_period.rescale(pq.ms).magnitude
        metadata['n'] = n
        data = numpy.empty((n, 2))
        # if the 'variable' annotation is a standard one from PyNN, we rescale
        # to use standard PyNN units
        # we take the units from the first element of source and scale all
        # the signals to have the same units
        if 'variable' in segment.annotations:
            units = UNITS_MAP.get(segment.annotations['variable'], source[0].dimensionality)
        else:
            units = source[0].dimensionality
            metadata['variable'] = 'unknown'
        try:
            metadata['units'] = units.unicode
        except AttributeError:
            metadata['units'] = units.u_symbol

        start = 0
        for i, signal in enumerate(source):  # here signal may be AnalogSignal or SpikeTrain
            end = start + signal.size
            data[start:end, 0] = numpy.array(signal.rescale(units))
            data[start:end, 1] = i * numpy.ones((signal.size,), dtype=float)
            start = end
        self._write_file_contents(data, metadata)

    def read_analogsignal(self, lazy=False):
        assert not lazy, 'Do not support lazy'

        data, metadata = self._read_file_contents()
        if metadata['variable'] == 'spikes':
            raise TypeError("File contains spike data, not analog signals")
        else:
            signal = self._extract_signals(data, metadata)
            if signal is None:
                raise IndexError("File does not contain a signal")
            else:
                return signal

    def read_spiketrain(self, lazy=False, channel_index=0):
        assert not lazy, 'Do not support lazy'
        data, metadata = self._read_file_contents()
        if metadata['variable'] != 'spikes':
            raise TypeError("File contains analog signals, not spike data")
        else:
            spiketrain = self._extract_spikes(data, metadata, channel_index)
            if spiketrain is None:
                raise IndexError(
                    "File does not contain any spikes with channel index %d" % channel_index)
            else:
                return spiketrain


class PyNNNumpyIO(BasePyNNIO):
    """
    (DEPRECATED) Reads/writes data from/to PyNN NumpyBinaryFile format
    """
    name = "PyNN NumpyBinaryFile"
    extensions = ['npz']

    def _read_file_contents(self):
        contents = numpy.load(self.filename)
        data = contents["data"]
        metadata = {}
        for name, value in contents['metadata']:
            try:
                metadata[name] = eval(value)
            except Exception:
                metadata[name] = value
        return data, metadata

    def _write_file_contents(self, data, metadata):
        # we explicitly set the dtype to ensure roundtrips preserve file contents exactly
        max_metadata_length = max(chain([len(k) for k in metadata.keys()],
                                        [len(str(v)) for v in metadata.values()]))
        if PY2:
            dtype = "S%d" % max_metadata_length
        else:
            dtype = "U%d" % max_metadata_length
        metadata_array = numpy.array(sorted(metadata.items()), dtype)
        numpy.savez(self.filename, data=data, metadata=metadata_array)


class PyNNTextIO(BasePyNNIO):
    """
    (DEPRECATED) Reads/writes data from/to PyNN StandardTextFile format
    """
    name = "PyNN StandardTextFile"
    extensions = ['v', 'ras', 'gsyn']

    def _read_metadata(self):
        metadata = {}
        with open(self.filename) as f:
            for line in f:
                if line[0] == "#":
                    name, value = line[1:].strip().split("=")
                    name = name.strip()
                    try:
                        metadata[name] = eval(value)
                    except Exception:
                        metadata[name] = value.strip()
                else:
                    break
        return metadata

    def _read_file_contents(self):
        data = numpy.loadtxt(self.filename)
        metadata = self._read_metadata()
        return data, metadata

    def _write_file_contents(self, data, metadata):
        with open(self.filename, 'wb') as f:
            for item in sorted(metadata.items()):
                f.write(("# %s = %s\n" % item).encode('utf8'))
            numpy.savetxt(f, data)
