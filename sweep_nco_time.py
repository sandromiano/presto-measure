import os
import sys
import time

import h5py
import numpy as np

import commands as cmd
import rflockin
from utils import get_sourcecode, format_sec
import load_sweep_nco_time

JPA = False
if JPA:
    if '/home/riccardo/IntermodulatorSuite' not in sys.path:
        sys.path.append('/home/riccardo/IntermodulatorSuite')
    from mlaapi import mla_api
    from mlaapi import mla_globals
    settings = mla_globals.read_config()
    mla = mla_api.MLA(settings)

# Presto's IP address or hostname
ADDRESS = "130.237.35.90"
PORT = 42878
EXT_CLK_REF = False

output_port = [1, 9]
input_port = 1

# amp = 0.707
amp = 1e-3  # FS
phase = 0.0
dither = True

extra = 500
# f_start = 5.6e9
# f_stop = 7e9
f_center = 6.029 * 1e9
f_span = 5 * 1e6
f_start = f_center - f_span / 2
f_stop = f_center + f_span / 2
df = 1e4  # Hz
Navg = 10

if JPA:
    jpa_pump_freq = 2 * 6.031e9  # Hz
    jpa_pump_pwr = 7  # lmx units
    jpa_bias = +0.432  # V
    bias_port = 1
    mla.connect()

with rflockin.Test(
        address=ADDRESS,
        port=PORT,
        ext_ref_clk=EXT_CLK_REF,
        adc_mode=cmd.AdcMixed,
        adc_fsample=cmd.AdcG2,
        dac_mode=cmd.DacMixed42,
        dac_fsample=cmd.DacG10,
) as lck:
    lck.hardware.set_adc_attenuation(input_port, 0.0)
    # lck.hardware.set_dac_current(output_port, 6_425)
    lck.hardware.set_dac_current(output_port, 32_000)
    lck.hardware.set_inv_sinc(output_port, 0)

    if JPA:
        lck.hardware.set_lmx(jpa_pump_freq, jpa_pump_pwr)
        mla.lockin.set_dc_offset(bias_port, jpa_bias)
        time.sleep(1.0)
    else:
        lck.hardware.set_lmx(0.0, 0)

    fs = lck.get_fs()
    nr_samples = int(round(fs / df))
    df = fs / nr_samples

    n_start = int(round(f_start / df))
    n_stop = int(round(f_stop / df))
    n_arr = np.arange(n_start, n_stop + 1)
    nr_freq = len(n_arr)
    freq_arr = df * n_arr
    resp_arr = np.zeros(nr_freq, np.complex128)

    lck.hardware.set_run(False)
    lck.hardware.configure_mixer(
        freq=freq_arr[0],
        in_ports=input_port,
        out_ports=output_port,
    )
    lck.set_frequency(output_port, 0.0)
    lck.set_scale(output_port, amp, amp)
    lck.set_phase(output_port, phase, phase)
    lck.set_dither(output_port, dither)
    lck.set_dma_source(input_port)
    lck.hardware.set_run(True)

    t_start = time.time()
    t_last = t_start
    prev_print_len = 0
    print()
    for ii in range(len(n_arr)):
        # lck.hardware.sleep(1e-1, False)
        f = freq_arr[ii]

        lck.hardware.set_run(False)
        lck.hardware.configure_mixer(
            freq=f,
            in_ports=input_port,
            out_ports=output_port,
        )
        lck.hardware.sleep(1e-3, False)
        lck.start_dma(Navg * nr_samples + extra)
        lck.hardware.set_run(True)
        lck.wait_for_dma()
        lck.stop_dma()

        _data = lck.get_dma_data(Navg * nr_samples + extra)
        data_i = _data[0::2][-Navg * nr_samples:] / 32767
        data_q = _data[1::2][-Navg * nr_samples:] / 32767

        data_i.shape = (Navg, nr_samples)
        data_q.shape = (Navg, nr_samples)
        data_i = np.mean(data_i, axis=0)
        data_q = np.mean(data_q, axis=0)

        avg_i = np.mean(data_i)
        avg_q = np.mean(data_q)
        resp_arr[ii] = avg_i + 1j * avg_q

        # Calculate and print remaining time
        t_now = time.time()
        if t_now - t_last > np.pi / 3 / 5:
            t_last = t_now
            t_sofar = t_now - t_start
            nr_sofar = ii + 1
            nr_left = nr_freq - ii - 1
            t_avg = t_sofar / nr_sofar
            t_left = t_avg * nr_left
            str_left = format_sec(t_left)
            msg = "Time remaining: {:s}".format(str_left)
            print_len = len(msg)
            if print_len < prev_print_len:
                msg += " " * (prev_print_len - print_len)
            print(msg, end="\r", flush=True)
            prev_print_len = print_len

    # Mute outputs at the end of the sweep
    lck.hardware.set_run(False)
    lck.set_scale(output_port, 0.0, 0.0)
    lck.hardware.set_lmx(0.0, 0)

if JPA:
    mla.lockin.set_dc_offset(bias_port, 0.0)
    mla.disconnect()

# *************************
# *** Save data to HDF5 ***
# *************************
script_filename = os.path.splitext(os.path.basename(__file__))[0]  # name of current script
timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())  # current date and time
save_filename = f"{script_filename:s}_{timestamp:s}.h5"  # name of save file
source_code = get_sourcecode(__file__)  # save also the sourcecode of the script for future reference
with h5py.File(save_filename, "w") as h5f:
    dt = h5py.string_dtype(encoding='utf-8')
    ds = h5f.create_dataset("source_code", (len(source_code), ), dt)
    for ii, line in enumerate(source_code):
        ds[ii] = line
    h5f.attrs["df"] = df
    h5f.attrs["amp"] = amp
    h5f.attrs["phase"] = phase
    h5f.attrs["dither"] = dither
    h5f.attrs["input_port"] = input_port
    h5f.attrs["output_port"] = output_port
    h5f.create_dataset("freq_arr", data=freq_arr)
    h5f.create_dataset("resp_arr", data=resp_arr)

# *****************
# *** Plot data ***
# *****************
fig1, span_a, span_p = load_sweep_nco_time.load(save_filename)