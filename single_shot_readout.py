# -*- coding: utf-8 -*-
"""
Measure Rabi oscillation by changing the amplitude of the control pulse.

The control pulse has a sin^2 envelope, while the readout pulse is square.
"""
import ast
import math
from typing import List, Tuple

import h5py
import numpy as np

from presto.hardware import AdcFSample, AdcMode, DacFSample, DacMode
from presto import pulsed
from presto.utils import format_precision, rotate_opt, sin2

from _base import Base

DAC_CURRENT = 32_000  # uA
CONVERTER_CONFIGURATION = {
    "adc_mode": AdcMode.Mixed,
    "adc_fsample": AdcFSample.G2,
    "dac_mode": [DacMode.Mixed42, DacMode.Mixed02, DacMode.Direct, DacMode.Mixed02],
    "dac_fsample": [DacFSample.G10, DacFSample.G6, DacFSample.G2, DacFSample.G6],
}


class SingleShotReadout(Base):
    def __init__(
        self,
        readout_freq: float,
        control_freq: float,
        readout_amp: float,
        control_amp: float,
        readout_duration: float,
        control_duration: float,
        sample_duration: float,
        readout_port: int,
        control_port: int,
        sample_port: int,
        wait_delay: float,
        readout_sample_delay: float,
        num_averages: int,
        template_match_start: float,
        template_match_duration=None,
        template_match_phase=0.0,
        drag: float = 0.0,
    ) -> None:
        self.readout_freq = readout_freq
        self.control_freq = control_freq
        self.readout_amp = readout_amp
        self.control_amp = control_amp
        self.readout_duration = readout_duration
        self.control_duration = control_duration
        self.sample_duration = sample_duration
        self.readout_port = readout_port
        self.control_port = control_port
        self.sample_port = sample_port
        self.wait_delay = wait_delay
        self.readout_sample_delay = readout_sample_delay
        self.num_averages = num_averages
        self.template_match_start = template_match_start
        if template_match_duration == None:
            self.template_match_duration = sample_duration
        else:
            self.template_match_duration = template_match_duration
        self.template_match_phase = template_match_phase
        self.drag = drag

        self.t_arr = None  # replaced by run
        self.store_arr = None  # replaced by run
        self.match_arr = None  # replaced by run

    def run(
        self,
        presto_address: str,
        presto_port: int = None,
        ext_ref_clk: bool = False,
    ) -> str:
        # Instantiate interface class
        with pulsed.Pulsed(
            address=presto_address,
            port=presto_port,
            ext_ref_clk=ext_ref_clk,
            **CONVERTER_CONFIGURATION,
        ) as pls:
            assert pls.hardware is not None

            pls.hardware.set_adc_attenuation(self.sample_port, 0.0)
            pls.hardware.set_dac_current(self.readout_port, DAC_CURRENT)
            pls.hardware.set_dac_current(self.control_port, DAC_CURRENT)
            pls.hardware.set_inv_sinc(self.readout_port, 0)
            pls.hardware.set_inv_sinc(self.control_port, 0)

            pls.hardware.configure_mixer(
                self.readout_freq,
                in_ports=self.sample_port,
                out_ports=self.readout_port,
                sync=False,
            )  # sync in next call

            pls.hardware.configure_mixer(
                self.control_freq, out_ports=self.control_port, sync=True
            )  # sync here

            # ************************************
            # *** Setup measurement parameters ***
            # ************************************
            # Setup lookup tables for amplitudes
            pls.setup_scale_lut(self.readout_port, group=0, scales=self.readout_amp)
            pls.setup_scale_lut(self.control_port, group=0, scales=[0, self.control_amp])

            # Setup readout and control pulses
            # use setup_long_drive to create a pulse with square envelope
            # setup_long_drive supports smooth rise and fall transitions for the pulse,
            # but we keep it simple here
            readout_pulse = pls.setup_long_drive(
                self.readout_port,
                group=0,
                duration=self.readout_duration,
                amplitude=1.0 + 1j,
                envelope=False,
            )

            # For the control pulse we create a sine-squared envelope,
            # and use setup_template to use the user-defined envelope
            # number of samples in the control template
            control_ns = int(round(self.control_duration * pls.get_fs("dac")))
            control_envelope = sin2(control_ns, drag=self.drag)
            control_pulse = pls.setup_template(
                self.control_port,
                group=0,
                template=control_envelope + 1j * control_envelope,
                envelope=False,
            )

            # Setup sampling window
            pls.set_store_ports(self.sample_port)
            pls.set_store_duration(self.sample_duration)

            # Setup template matching
            shape = np.ones(int(round(self.template_match_duration * pls.get_fs("dac"))))
            match_events = pls.setup_template_matching_pair(
                input_port=self.sample_port,
                template1=shape,
                template2=1j * shape,
            )

            # ******************************
            # *** Program pulse sequence ***
            # ******************************
            T = 0.0  # s, start at time zero ...
            for ii in range(2):
                pls.select_scale(T, ii, self.control_port, group=0)
                pls.output_pulse(T, [control_pulse])
                T += self.control_duration

                pls.reset_phase(T, self.readout_port)
                pls.output_pulse(T, [readout_pulse])
                pls.store(T + self.readout_sample_delay)
                pls.match(T + self.readout_sample_delay, [match_events])
                T += self.readout_duration + self.wait_delay

            # **************************
            # *** Run the experiment ***
            # **************************

            pls.run(period=T, repeat_count=1, num_averages=self.num_averages)
            self.t_arr, self.store_arr = pls.get_store_data()
            self.match_arr = pls.get_template_matching_data([match_events])

        return self.save()

    def save(self, save_filename: str = None) -> str:
        return super().save(__file__, save_filename=save_filename)

    @classmethod
    def load(cls, load_filename: str) -> "SingleShotReadout":
        with h5py.File(load_filename, "r") as h5f:
            readout_freq = h5f.attrs["readout_freq"]
            control_freq = h5f.attrs["control_freq"]
            readout_amp = h5f.attrs["readout_amp"]
            control_amp = h5f.attrs["control_amp"]
            readout_duration = h5f.attrs["readout_duration"]
            control_duration = h5f.attrs["control_duration"]
            sample_duration = h5f.attrs["sample_duration"]
            readout_port = h5f.attrs["readout_port"]
            control_port = h5f.attrs["control_port"]
            sample_port = h5f.attrs["sample_port"]
            wait_delay = h5f.attrs["wait_delay"]
            readout_sample_delay = h5f.attrs["readout_sample_delay"]
            template_match_start = h5f.attrs["template_match_start"]
            template_match_duration = h5f.attrs["template_match_duration"]
            num_averages = h5f.attrs["num_averages"]

            t_arr = h5f["t_arr"][()]
            store_arr = h5f["store_arr"][()]
            match_arr = h5f["match_arr"][()]

            try:
                drag = h5f.attrs["drag"]
            except KeyError:
                drag = 0.0

        self = cls(
            readout_freq=readout_freq,
            control_freq=control_freq,
            readout_amp=readout_amp,
            control_amp=control_amp,
            readout_duration=readout_duration,
            control_duration=control_duration,
            sample_duration=sample_duration,
            readout_port=readout_port,
            control_port=control_port,
            sample_port=sample_port,
            wait_delay=wait_delay,
            readout_sample_delay=readout_sample_delay,
            template_match_start=template_match_start,
            template_match_duration=template_match_duration,
            num_averages=num_averages,
            drag=drag,
        )
        self.t_arr = t_arr
        self.store_arr = store_arr
        self.match_arr = match_arr

        return self

    def analyze(self, portrait: bool = True, all_plots: bool = False):
        if self.t_arr is None:
            raise RuntimeError
        if self.store_arr is None:
            raise RuntimeError
        if self.match_arr is None:
            raise RuntimeError
        import matplotlib.pyplot as plt

        ret_fig = []

        fs = 1 / (self.t_arr[1] - self.t_arr[0])
        IDX_LOW = int(round(self.template_match_start * fs))
        IDX_HIGH = int(round((self.template_match_start + self.template_match_duration) * fs))
        t_low = self.t_arr[IDX_LOW]
        t_high = self.t_arr[IDX_HIGH]

        if all_plots:
            # Plot raw store data for first iteration as a check
            fig1, ax1 = plt.subplots(2, 1, sharex=True, tight_layout=True)
            ax11, ax12 = ax1
            ax11.axvspan(1e9 * t_low, 1e9 * t_high, facecolor="#dfdfdf")
            ax12.axvspan(1e9 * t_low, 1e9 * t_high, facecolor="#dfdfdf")
            ax11.plot(1e9 * self.t_arr, np.abs(self.store_arr[0, 0, :]))
            ax12.plot(1e9 * self.t_arr, np.angle(self.store_arr[0, 0, :]))
            ax12.set_xlabel("Time [ns]")
            fig1.show()
            ret_fig.append(fig1)

        fig2 = plt.figure(tight_layout=True)
        ax1 = fig2.add_subplot(1, 1, 1)

        i_data = self.match_arr[0]
        q_data = self.match_arr[1]
        angle = 0
        ground_data = (i_data[::2] + 1j * q_data[::2]) * np.exp(1j * angle)
        excited_data = (i_data[1::2] + 1j * q_data[1::2]) * np.exp(1j * angle)
        ground_avg_data = np.sum(self.store_arr[0, 0, IDX_LOW:IDX_HIGH])
        excited_avg_data = np.sum(self.store_arr[1, 0, IDX_LOW:IDX_HIGH])

        ax1.plot(ground_data.real, ground_data.imag, ".", alpha=0.2, label="ground")
        ax1.plot(excited_data.real, excited_data.imag, ".", alpha=0.2, label="excited")
        ax1.plot(ground_avg_data.real, ground_avg_data.imag, ".", alpha=1)
        ax1.plot(excited_avg_data.real, excited_avg_data.imag, ".", alpha=1)
        ax1.legend()
        ax1.set_aspect("equal", adjustable="box")
        ax1.set_xlabel("In phase [FS]")
        ax1.set_ylabel("Quadrature [FS]")
        ret_fig.append(fig2)

        return ret_fig