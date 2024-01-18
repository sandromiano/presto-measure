# -*- coding: utf-8 -*-
"""Measure the energy-relaxation time T1."""
from typing import List, Optional

import h5py
import numpy as np

from presto.hardware import AdcMode, DacMode
from presto import pulsed
from presto.utils import format_precision, rotate_opt, sin2

from _base import Base, project

DAC_CURRENT = 32_000  # uA
CONVERTER_CONFIGURATION = {
    "adc_mode": AdcMode.Mixed,
    "dac_mode": DacMode.Mixed,
}
IDX_LOW = 0
IDX_HIGH = -1


class T1_cavity_coherent(Base):
    def __init__(
        self,
        readout_freq: float,
        control_freq: float,
        cavity_freq: float,
        readout_amp: float,
        control_amp: float,
        cavity_amp: float,
        readout_duration: float,
        control_duration: float,
        cavity_duration: float,
        sample_duration: float,
        delay_arr: List[float],
        readout_port: int,
        control_port: int,
        cavity_port: int,
        sample_port: int,
        wait_delay: float,
        readout_sample_delay: float,
        num_averages: int,
    ) -> None:
        self.readout_freq = readout_freq
        self.control_freq = control_freq
        self.cavity_freq = cavity_freq
        self.readout_amp = readout_amp
        self.control_amp = control_amp
        self.cavity_amp = cavity_amp
        self.readout_duration = readout_duration
        self.control_duration = control_duration
        self.cavity_duration = cavity_duration
        self.sample_duration = sample_duration
        self.delay_arr = np.atleast_1d(delay_arr).astype(np.float64)
        self.readout_port = readout_port
        self.control_port = control_port
        self.cavity_port = cavity_port
        self.sample_port = sample_port
        self.wait_delay = wait_delay
        self.readout_sample_delay = readout_sample_delay
        self.num_averages = num_averages

        self.t_arr = None  # replaced by run
        self.store_arr = None  # replaced by run

    def run(
        self,
        presto_address: str,
        presto_port: int = None,
        ext_ref_clk: bool = False,
        save: bool = True,
    ) -> str:
        # Instantiate interface class
        with pulsed.Pulsed(
            address=presto_address,
            port=presto_port,
            ext_ref_clk=ext_ref_clk,
            **CONVERTER_CONFIGURATION,
        ) as pls:
            pls.hardware.set_adc_attenuation(self.sample_port, 0.0)
            pls.hardware.set_dac_current(self.readout_port, DAC_CURRENT)
            pls.hardware.set_dac_current(self.control_port, DAC_CURRENT)
            pls.hardware.set_dac_current(self.cavity_port, DAC_CURRENT)
            pls.hardware.set_inv_sinc(self.readout_port, 0)
            pls.hardware.set_inv_sinc(self.control_port, 0)
            pls.hardware.set_inv_sinc(self.cavity_port, 0)

            pls.hardware.configure_mixer(
                self.readout_freq,
                in_ports=self.sample_port,
                out_ports=self.readout_port,
                sync=False,
            )  # sync in next call

            pls.hardware.configure_mixer(
                self.control_freq, out_ports=self.control_port, sync=False
            )
            pls.hardware.configure_mixer(
                self.cavity_freq, out_ports=self.cavity_port, sync=True
            )  # sync here

            # ************************************
            # *** Setup measurement parameters ***
            # ************************************

            # Setup lookup tables for amplitudes
            pls.setup_scale_lut(self.readout_port, group=0, scales=self.readout_amp)
            pls.setup_scale_lut(self.control_port, group=0, scales=self.control_amp)
            pls.setup_scale_lut(self.cavity_port, group=0, scales=self.cavity_amp)

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

            # number of samples in the control template
            control_ns = int(round(self.control_duration * pls.get_fs("dac")))
            control_envelope = sin2(control_ns)
            control_pulse = pls.setup_template(
                self.control_port,
                group=0,
                template=control_envelope + 1j * control_envelope,
                envelope=False,
            )

            cavity_ns = int(round(self.cavity_duration * pls.get_fs("dac")))
            cavity_envelope = sin2(cavity_ns)
            cavity_pulse = pls.setup_template(
                self.cavity_port,
                group=0,
                template=cavity_envelope + 1j * cavity_envelope,
                envelope=False,
            )

            # Setup sampling window
            pls.set_store_ports(self.sample_port)
            pls.set_store_duration(self.sample_duration)

            # ******************************
            # *** Program pulse sequence ***
            # ******************************
            T = 0.0  # s, start at time zero ...
            for delay in self.delay_arr:
                pls.output_pulse(T, cavity_pulse)  # displace cavity
                T += self.cavity_duration
                T += delay  # increasing delay
                pls.output_pulse(T, control_pulse)  # pi pulse conditioned on cavity in |0>
                T += self.control_duration
                pls.output_pulse(T, readout_pulse)  # Readout
                pls.store(T + self.readout_sample_delay)
                T += self.readout_duration
                T += self.wait_delay  # Wait for decay

            # **************************
            # *** Run the experiment ***
            # **************************
            pls.run(period=T, repeat_count=1, num_averages=self.num_averages)
            self.t_arr, self.store_arr = pls.get_store_data()

        return self.save()

    def save(self, save_filename: str = None) -> str:
        return super()._save(__file__, save_filename=save_filename)

    @classmethod
    def load(cls, load_filename: str) -> "T1_cavity_coherent":
        with h5py.File(load_filename, "r") as h5f:
            readout_freq = h5f.attrs["readout_freq"]
            control_freq = h5f.attrs["control_freq"]
            cavity_freq = h5f.attrs["cavity_freq"]
            readout_amp = h5f.attrs["readout_amp"]
            control_amp = h5f.attrs["control_amp"]
            cavity_amp = h5f.attrs["cavity_amp"]
            readout_duration = h5f.attrs["readout_duration"]
            control_duration = h5f.attrs["control_duration"]
            cavity_duration = h5f.attrs["cavity_duration"]
            sample_duration = h5f.attrs["sample_duration"]
            delay_arr = h5f["delay_arr"][()]
            readout_port = h5f.attrs["readout_port"]
            control_port = h5f.attrs["control_port"]
            cavity_port = h5f.attrs["cavity_port"]
            sample_port = h5f.attrs["sample_port"]
            wait_delay = h5f.attrs["wait_delay"]
            readout_sample_delay = h5f.attrs["readout_sample_delay"]
            num_averages = h5f.attrs["num_averages"]

            t_arr = h5f["t_arr"][()]
            store_arr = h5f["store_arr"][()]

        self = cls(
            readout_freq=readout_freq,
            control_freq=control_freq,
            cavity_freq=cavity_freq,
            readout_amp=readout_amp,
            control_amp=control_amp,
            cavity_amp=cavity_amp,
            readout_duration=readout_duration,
            control_duration=control_duration,
            cavity_duration=cavity_duration,
            sample_duration=sample_duration,
            delay_arr=delay_arr,
            readout_port=readout_port,
            control_port=control_port,
            cavity_port=cavity_port,
            sample_port=sample_port,
            wait_delay=wait_delay,
            readout_sample_delay=readout_sample_delay,
            num_averages=num_averages,
        )
        self.t_arr = t_arr
        self.store_arr = store_arr

        return self

    def analyze_batch(self, reference_templates: Optional[tuple] = None):
        assert self.t_arr is not None
        assert self.store_arr is not None

        if reference_templates is None:
            resp_arr = np.mean(self.store_arr[:, 0, IDX_LOW:IDX_HIGH], axis=-1)
            data = np.real(rotate_opt(resp_arr))
        else:
            resp_arr = self.store_arr[:, 0, :]
            data = project(resp_arr, reference_templates)

        try:
            popt, perr = _fit_simple(self.delay_arr, data)
        except Exception as err:
            print(f"unable to fit T1: {err}")
            popt, perr = None, None

        return data, (popt, perr)

    def analyze(self, beta: float, all_plots: bool = False):
        assert self.t_arr is not None
        assert self.store_arr is not None

        import matplotlib.pyplot as plt

        ret_fig = []
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

        # Analyze T1
        resp_arr = np.mean(self.store_arr[:, 0, IDX_LOW:IDX_HIGH], axis=-1)
        resp_arr = rotate_opt(resp_arr)

        # Fit data
        popt, perr = _fit_simple(self.delay_arr, np.real(resp_arr), beta)

        T1 = popt[0]
        T1_err = perr[0]
        print("T1 time I: {} +- {} us".format(1e6 * T1, 1e6 * T1_err))

        if all_plots:
            fig2, ax2 = plt.subplots(4, 1, sharex=True, figsize=(6.4, 6.4), tight_layout=True)
            ax21, ax22, ax23, ax24 = ax2
            ax21.plot(1e6 * self.delay_arr, np.abs(resp_arr))
            ax22.plot(1e6 * self.delay_arr, np.unwrap(np.angle(resp_arr)))
            ax23.plot(1e6 * self.delay_arr, np.real(resp_arr))
            ax23.plot(1e6 * self.delay_arr, _decay(self.delay_arr, *popt), "--")
            ax24.plot(1e6 * self.delay_arr, np.imag(resp_arr))

            ax21.set_ylabel("Amplitude [FS]")
            ax22.set_ylabel("Phase [rad]")
            ax23.set_ylabel("I [FS]")
            ax24.set_ylabel("Q [FS]")
            ax2[-1].set_xlabel("Control-readout delay [μs]")
            fig2.show()
            ret_fig.append(fig2)

        # bigger plot just for I quadrature
        data_max = np.abs(resp_arr.real).max()
        unit = ""
        mult = 1.0
        if data_max < 1e-6:
            unit = "n"
            mult = 1e9
        elif data_max < 1e-3:
            unit = "μ"
            mult = 1e6
        elif data_max < 1e0:
            unit = "m"
            mult = 1e3

        fig3, ax3 = plt.subplots(tight_layout=True)
        ax3.plot(1e6 * self.delay_arr, mult * np.real(resp_arr), ".")
        ax3.plot(1e6 * self.delay_arr, mult * _decay(self.delay_arr, beta, *popt), "--")
        ax3.set_ylabel(f"I quadrature [{unit:s}FS]")
        ax3.set_xlabel(r"Delay [μs]")
        ax3.set_title("T1 = {:s} μs".format(format_precision(1e6 * T1, 1e6 * T1_err)))
        fig3.show()
        ret_fig.append(fig3)

        return ret_fig


def _decay(t, beta, *p):
    T1, xe, xg = p
    return xg + (xe - xg) * np.exp(-(np.abs(beta) ** 2) * np.exp(-t / T1))


def _fit_simple(t, x, beta):
    from scipy.optimize import curve_fit

    def my_decay(t, *p):
        return _decay(t, beta, *p)

    T1 = 0.25 * (t[-1] - t[0])
    xe, xg = x[0], x[-1]
    p0 = (T1, xe, xg)
    popt, pcov = curve_fit(my_decay, t, x, p0)
    perr = np.sqrt(np.diag(pcov))
    return popt, perr
