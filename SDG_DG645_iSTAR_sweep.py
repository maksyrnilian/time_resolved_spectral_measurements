import socket
import time
import shutil
from pathlib import Path
from datetime import date

# =============================================================================
# =============================================================================
#  DG645 Gate Sweeping for iStar
#  ----------------------------------------------------------
#  WHAT IT DOES
#  ----------------------------------------------------------
#  1. Creates a fresh run subfolder  <data_folder>/YYYYMMDD_<sample>_<i>  (i from 1).
#  2. Generates the AndorBasic acquisition program AS TEXT from the SAME
#     GATE_DELAY_STEPS it will sweep, so the .sif filenames (which encode each
#     step's delay) and the gate sweep can never disagree. The program is saved
#     directly into the run subfolder as a .txt file, with a COMMENT embedded at the
#     top as a provenance record.
#  3. Configures the DG645 (idle) and then PAUSES, asking you to copy the .txt
#     into Solis and run it (the camera arms and waits). You type 'yes'/'y' to start
#     the sweep, or anything else to abort.
#  4. On 'yes', runs the gated sweep: for each step it sets the gate delay and starts
#     sending the gating signal, then briefly raises the camera trigger (AB) so the
#     camera starts capturing, with the gate (CD) running underneath. Once the set
#     acquisition time has elapsed, Solis takes some time to read the sensor data and
#     save the .sif file while the DG645 waits idle, before the next step commences.
#     The sweep is fully automatic, no user intervention is needed, and the remaining
#     time is displayed in the terminal (worth watching on a long sweep).
#  5. Verifies that the number of .sif files written equals the number of steps, so the
#     filenames do not drift. Drift can happen if Solis takes more than IDLE_TIME_S to
#     read, save and re-arm.
#
#
#  TIME REFERENCE
#  --------------
#  GATE_DELAY_STEPS are measured FROM THE OPTICAL PULSE hitting the sample:
#  a value of 0 means "gate begins on the pulse", assuming a correct OFFSET_C_DELAY.
#  OFFSET_C_DELAY is a fixed delay accounting for the SDG's T0 -> pulse leaving the
#  laser plus delays of light travel, electronics, DG645 insertion delay etc. The
#  actual DG645 gate-start delay is C = step + OFFSET_C_DELAY, measured relative to the
#  sync signal from the SDG. The filename shows the step value, but the offset is noted
#  in the Andor program in case it is needed.
#
#
#  TRIGGER/GATE CHANNELS
#  ----------------------------------------------
#  DG645 is slaved to the SDG (channel ? on SDG).
#  AB -> iStar EXT TRIG,
#  CD -> iStar Direct Gate,
#  EF -> identical to CD (for monitoring on the scope).
#
#  We switch each channel on/off via its amplitude:
#  ACTIVE_LEVEL_V reads as TTL logic HIGH, OFF_LEVEL_V (0.5 V) as LOW (can't be lower).
#  AndorBasic `Run()` waits for a TTL-high trigger edge, so keeping the amplitude low
#  prevents triggering/gating.
#
#
#  POINTS TO WATCH
#  -------------------------------------------------------
#  * VOLTAGES: iStar EXT TRIG and Gate are 74AC TTL, 50 ohm, <=5 V.
#  * If Solis acquisition never terminates it likely means that readout was too long,
#    a frame was missed and files are mislabelled. Delete the measurements and increase
#    IDLE_TIME_S.
#  * Camera needs to be set in Solis to EXTERNAL TRIGGER and FIRE AND GATE. The latter
#    means that the intensifier is allowed to open only when both the external gating
#    signal and the camera's active exposure window align, preventing image smearing
#    during sensor readout. GATE ONLY would also work but possible illumination during
#    sensor readout would smear the image.
#  * If someone (Przemek) changes the Pockels cells' delays on the SDG, the
#    OFFSET_C_DELAY must be re-calibrated. The proposed way to do it is to measure the
#    scatter of the laser on a diffuse glass slide, with a gate width of 3ns. The SP-750
#    needs to be removed from behind the objective and an ND filter placed there instead.
#    Moreover another ND filter and a rotatable linear polarizer should be used for
#    maximum attenuation. If you do not register any laser, 99% chance is that something
#    is misaligned, not that the attenuation is too strong.
#  * For compatibility of filenames, numers are converted: `-50.5e-9 s' -> `m50p5ns'
#
#
#  SUGGESTED WORKFLOW
#  -------------------------------------------------------
#  1. Cover the laser and place the sample. Set the camera to EXTERNAL TRIGGER + FIRE AND GATE.
#  2. Minimise the laser power on the HWP.
#  3. Check the laser average power with a meter and the flipper mirror.
#  4. Enter the power value below into POWER_MW in milliwatts.
#  5. Name the sample (for the file name) and enter a comment describing the measurement series (like "I saw smoke" or "likely misaligned").
#  6. Run the script. It will create a new run folder and generate the AndorBasic program text.
#  7. CHECK THE SETTINGS in Solis, copy the generated .txt into Solis (NEW PROGRAM / Ctrl + n -> Run Program). The camera will arm and wait for the sweep to start.
#  8. Make sure the laser is unblocked.
#  9. Type 'y' in the terminal to start.
# =============================================================================
# =============================================================================




# ============================ USER SETTINGS ==================================
# Parent data folder for all runs. MUST be a raw string (r"..."). Flashdrive recommended.
DATA_FOLDER = r"R:\measurements"

# Sample name -> goes into the run-folder name and into each .sif filename.
SAMPLE_NAME = "UCNP"

# Free-text comment describing this measurement series. It is embedded at the top
# of the generated Andor program as a provenance record. Use ASCII 
COMMENT = """
    Finally superfluorescence?
    """

# Laser power for this measurement series, in MILLIWATTS (mW). OPTIONAL (Set to None if want to skip):
POWER_MW = 50

def even_delay_grid(start, step, count):    # Helper to build an evenly spaced grid of gate delays instead of listing them by hand.
    return [start + i * step for i in range(count)]

GATE_DELAY_STEPS = [    # Gate delays for the sweep, measured FROM THE OPTICAL PULSE (0 = gate on the pulse).  These are the values that appear in the .sif filenames.
    50e-9,
    150e-9,
]

# Overwrites GATE_DELAY_STEPS above. Comment this line out to keep the custom list.
GATE_DELAY_STEPS = even_delay_grid(0, 0.25e-9, 4*4)

# Maximal gate width given 3kHz repetition is ~=300 000ns - gate_delay_step. 
# At values below 5ns, the pulse is clearly Gaussian-like and the actual gate is shorter, 3ns at 4.5V Higg and 1/1.5ns overlap shoul be safe,
GATE_WIDTH = 3e-9     

# OFFSET_C_DELAY positions the WHOLE sweep. It is the fixed delay from the SDG's T0 to the gate-start (C) that accounts for the SDG's internal delay, light travel, electronics, etc..
# Calibrate empirically (5219 was accurate in the beginning of July)
OFFSET_C_DELAY = 5197e-9 + 21e-9 + 1e-9  

# Gating signal is being sent for ACQUISITION_TIME_S + TRIGGER_CATCH_S (constant value <0.1s) to let the trigger register and the camera exposure is set to = ACQUISITION_TIME_S. In Fire and Gate mode it makes no difference.
ACQUISITION_TIME_S = 10  

# Number of separate acquisitions (frames/.sif files) to take at EACH gate delay.
ACQUISITIONS_PER_GATE = 2

# How the repeated acquisitions are ORDERED in time (G1(1) -> G1(2) -> G2(1) -> G2(2) vs G1(1) -> G2(1) -> G1(2) -> G2(2)). This is only relevant if ACQUISITIONS_PER_GATE > 1.
# The filenames are identical in both modes, only the temporal order differs. False is strongly preferred because it lets you monitor degradation during the sweep.
GROUP_EQUAL_GATE_ACQUISITIONS = False


# ============================ PARAMETERS ==================================
# Set the voltage levels corresponding to "active" (HIGH) and "off" (LOW) for the DG645 outputs.
# 3.5V < HIGH < 5V, 0.5V < LOW < 1.5V. The DG645 can only output 0.5 - 5 V, so the LOW level is set to the minimum allowed. 
# At shorter gates HIGH may affect effeective registed gate width. 4.5V at 3ns should be safe. I never went above 4.5V.
ACTIVE_LEVEL_V = 4.5     
OFF_LEVEL_V = 0.5        

# Time the trigger channel (AB) is held HIGH for each step. 0.1 works fine, lower should as well, doesn't really matter
TRIGGER_CATCH_S = 0.1

# Gate-off time; MUST exceed readout + save + re-arm (known to fail below ~5 s). If too short, a step is missed and labels shift -> caught by the 
# file-count check at the end (the Andor program will also likely never terminate).
IDLE_TIME_S = 10.0

# ============================ CONNECTION SETTINGS ==================================
# All of these are fine tor the particular PC and ports I used, may need to be changed if the setup is modified
DEVICE_IP = '192.168.1.6'
PORT = 5025
TIMEOUT = 5 

# Dictionary mapping BNC output names to their corresponding numbers for DG645 commands. 
# They are fixed by the DG645: T0=0, AB=1, CD=2, EF=3, GH=4.
# Don't change unless rewiring
OUTPUT_MAP = {'T0': 0, 'AB': 1, 'CD': 2, 'EF': 3, 'GH': 4}


# =========================== FUNCTIONS ===============================
def _ns_tag(x_seconds):
    """
    Converts a time (in seconds) into a short text label in nanoseconds,
    intended for insertion into a filename.
    Examples: 5.40e-6 s -> "5400ns", 5.4125e-6 s -> "5412p5ns",
              -50e-9 s -> "m50ns", 0 s -> "0ns".

    Why this way:
      * The decimal point is replaced by the letter 'p' (point), and the minus
        sign by the letter 'm' (minus). Reason: a dot in a filename can be
        confused with the extension (.sif), and some programs handle the
        characters '.' and '-' in the middle of a name poorly.

    Parameters:
    ----------
    x_seconds : float
        Time in seconds. May be positive, zero or negative.

    Returns:
    -------
    str
        Text label, e.g. "5400ns".
    """
    ns = round(x_seconds * 1e9, 1)        # *1e9: seconds -> nanoseconds; round(.,1): to 0.1 ns
    sign = "m" if ns < 0 else ""          # 'm' = negative value (minus)
    ns = abs(ns)                          # from here on we work with the positive value
    whole = int(ns)                       # integer part (e.g. 5412 from 5412.5)
    frac = int(round((ns - whole) * 10))  # first digit after the decimal point (e.g. 5 from 5412.5)
    if frac == 0:
        return f"{sign}{whole}ns"         # no fractional part, e.g. "5400ns"
    return f"{sign}{whole}p{frac}ns"      # with fractional part, e.g. "5412p5ns"


def _s_tag(x_seconds):
    """
    Converts a time in seconds into a short filename label, KEEPING the value
    in seconds (unlike _ns_tag, which converts to nanoseconds). Used for the
    exposure time.

    The decimal point is replaced by 'p' (a dot in a filename can be confused
    with the extension). Exposure is assumed to be >= 0, so there is no minus
    handling.

    Examples: 10 -> "10", 10.5 -> "10p5", 3.0 -> "3", 0.05 -> "0p05".

    Parameters:
    ----------
    x_seconds : float
        Exposure time in seconds.

    Returns:
    -------
    str
        Text label, e.g. "10p5".
    """
    return f"{x_seconds:g}".replace(".", "p")   # :g drops trailing zeros (3.0 -> "3")


def make_sif_basename(index, gate_start_s, gate_width_s, sample, exposure, count):
    """
    Builds the "stem" of the .sif filename (i.e. the name WITHOUT the extension) for a single measurement.
    This is the ONLY place to edit if you want a different filename scheme.

    Example result: "sampleA_idx003_g100ns_w50ns_exp3_count001"
      * sampleA -> sample name (so the file stays recognisable even after being moved).
      * idx003  -> step number, zero-padded to 3 digits. This makes the files
                   sort in measurement order (001, 002, 003, not 1, 10, 2).
      * g100ns  -> this step's gate delay (here: time after the pulse).
      * w50ns   -> gate width.
      * count001 -> acquisition number WITHIN this gate delay (1..ACQUISITIONS_PER_GATE),
                   zero-padded to 3 digits so repeats sort correctly.

    Parameters:
    ----------
    index : int
        Step number in the series (1, 2, 3, ...).
    gate_start_s : float
        Gate delay for this step [s] = value from GATE_DELAY_STEPS
        (time relative to the pulse). This is what goes into the filename.
    gate_width_s : float
        Gate width [s].
    sample : str
        Cleaned (safe) sample name - see the _safe() function.
    count : int
        Acquisition number within this gate delay (1, 2, ...).

    Returns:
    -------
    str
        The filename stem, without ".sif".
    """
    # The notation {index:03d} means: an integer zero-padded to 3 characters.
    return f"{sample}_idx{index:03d}_g{_ns_tag(gate_start_s)}_w{_ns_tag(gate_width_s)}_exp{_s_tag(exposure)}_count{count:03d}"


def _safe(name):
    """
    Converts any text into a version safe for use in a filename/folder name.
    The operating system does not accept certain characters in names (spaces, colons,
    slashes '/', '\\', question marks, etc.). Therefore every character OTHER than a
    letter, digit, hyphen '-' or underscore '_' is replaced with '_'.

    Example: "sample A!" -> "sample_A"

    Parameters:
    ----------
    name : str
        Raw text (e.g. SAMPLE_NAME entered by the user).

    Returns:
    -------
    str
        Cleaned text. If nothing remains after cleaning, returns "sample".
    """
    # The line below is a "list comprehension" - a shorthand for a loop building a list.
    # For each character c in the text 'name': if c is a letter/digit (c.isalnum())
    # or is one of the characters in "-_", we keep c; otherwise we insert '_'.
    # "".join(...) glues all those characters into a single string.
    cleaned = "".join(c if (c.isalnum() or c in "-_") else "_" for c in name)
    # .strip("_") removes underscores from the start and end.
    # 'result or "sample"' is a safeguard: if 'cleaned' is empty (only
    # disallowed characters), we return the default word "sample".
    return cleaned.strip("_") or "sample"


def send_command(s, cmd):
    """
    Sends a single text command to the DG645 (and prints it, should it be needed for debugging).

    Parameters:
    ----------
    s : socket.socket
        Active network connection (socket) to the DG645 device.
    cmd : str
        An SCPI-standard command, e.g. "*IDN?" or "TSRC 1". The function itself appends
        a newline character ("\\n") at the end, which the DG645 requires to accept the command.
    """
    try:
        s.sendall(f"{cmd}\n".encode())  # .encode() converts text into the bytes required by the network
        time.sleep(50e-3)                # short pause so as not to flood the buffer when sending quickly
    except Exception as e:
        print(f"Error sending command '{cmd}': {e}")


def set_delay(s, channel_name, delay_time):
    """
    Sets the delay of a given channel relative to the reference signal T0 (the DLAY command).

    Parameters:
    ----------
    s : socket.socket
        Active connection to the DG645.
    channel_name : str
        Channel letter: 'A', 'B', 'C' or 'D'.
    delay_time : float
        Delay time in seconds (measured from T0).
    """
    channel_map = {'A': 2, 'B': 3, 'C': 4, 'D': 5,
                   'E': 6, 'F': 7, 'G': 8, 'H': 9}  # internal delay-channel numbers
    if channel_name in channel_map:
        chan_num = channel_map[channel_name]
        # Command format: DLAY <channel>,<reference_channel=0(T0)>,<time in s>
        send_command(s, f"DLAY {chan_num},0,{delay_time}")
        print(f"   -> Channel {channel_name} = {delay_time} s")
    else:
        print(f"   [!] Unknown channel: {channel_name}")


def set_level(s, output_name, amplitude_v):
    """
    Sets the AMPLITUDE (height) of the pulse on a given DG645 output (the LAMP command).
    In this program we use amplitude as a software on/off switch for the channel:
      * ACTIVE_LEVEL_V (e.g. 4.0 V) -> the pulse is high enough for the camera's TTL
        input to read it as a HIGH state -> channel "on".
      * OFF_LEVEL_V (0.5 V) -> the pulse is below the TTL threshold, so the input sees a
        LOW state -> the channel is effectively "off" (even though the DG645 still
        pulses on every shot).

    Parameters:
    ----------
    s : socket.socket
        Active connection to the DG645.
    output_name : str
        BNC output name: 'T0', 'AB', 'CD', 'EF' or 'GH'.
    amplitude_v : float
        Amplitude in volts (DG645 allowed range: 0.5 - 5.0 V).
    """
    if output_name in OUTPUT_MAP:                  # is the given output name valid?
        b = OUTPUT_MAP[output_name]                # convert the name to a number (e.g. 'AB' -> 1)
        send_command(s, f"LAMP {b},{amplitude_v}") # LAMP <output_number>,<amplitude V>
        # Below is just a readable status printout: "ON" when the level is active, otherwise "off".
        state = "ON " if amplitude_v >= ACTIVE_LEVEL_V else "off"
        print(f"   -> Output {output_name}: {amplitude_v} V  [{state}]")
    else:
        print(f"   [!] Unknown output: {output_name}")


def configure_output_ttl(s, output_name):
    """
    Configures a DG645 output as a "clean" TTL signal: a square pulse from 0 V
    (low state) up to the set amplitude (high state). We do this ONCE, at the start.
    Two things are set:
      * LOFF (offset) = 0 V        -> the baseline (low state) sits at 0 V (ground).
      * LPOL (polarity) = 1        -> positive polarity: the pulse goes UP
                                      (0 V -> +amplitude). The camera input responds to
                                      a rising edge, so we want a positive pulse.
    The amplitude itself (pulse height) is set separately, via set_level().

    Parameters:
    ----------
    s : socket.socket
        Active connection to the DG645.
    output_name : str
        BNC output name: 'T0', 'AB', 'CD', 'EF' or 'GH'.
    """
    if output_name in OUTPUT_MAP:
        b = OUTPUT_MAP[output_name]
        send_command(s, f"LOFF {b},0")   # offset 0 V -> low state at ground level
        send_command(s, f"LPOL {b},1")   # 1 = positive polarity (pulse goes up)


# =========================== RUN SETUP / PLANNING ============================
def make_run_folder(parent_str, sample_safe, power_mw=None):
    """
    Creates a new measurement subfolder named  YYYYMMDD_<sample>_NN  inside
    the parent folder, where NN is the lowest free number (from 01), zero-padded
    to two digits. Leading zeros make the folders sort correctly
    (01, 02, ... 10, 11) rather than alphabetically (1, 10, 11, 2). Each run
    creates a separate, non-overwritable folder.

    If a laser power is given (power_mw is not None), a power tag is inserted
    BEFORE the run number, giving  YYYYMMDD_<sample>_<P>mW_NN . The number stays
    the LAST token so the run-index detection below is unaffected. When power_mw
    is None the name is exactly the original YYYYMMDD_<sample>_NN.

    Parameters:
    ----------
    parent_str : str
        Path to the parent folder (DATA_FOLDER).
    sample_safe : str
        Cleaned sample name (from the _safe() function).
    power_mw : float or None
        Laser power in mW. None -> no power tag.

    Returns:
    -------
    (folder, i, today, subfolder_name) : (pathlib.Path, int, str, str)
        Path of the created folder, its number (as an integer, e.g. 3), the date YYYYMMDD
        and the folder name itself (with the number zero-padded, e.g. "..._03").
    """
    parent = Path(parent_str)
    parent.mkdir(parents=True, exist_ok=True)         # create the parent folder if it does not exist
    today = date.today().strftime("%Y%m%d")           # today's date as text, e.g. "20260608"
    # Optional power tag, e.g. 50 -> "50mW", 12.5 -> "12p5mW" (dot -> 'p' via _s_tag).
    # Folded into the common prefix so the run number remains the final token.
    power_part = f"{_s_tag(power_mw)}mW_" if power_mw is not None else ""
    prefix = f"{today}_{sample_safe}_{power_part}"    # common name prefix for this sample/day

    # Scan the contents of the parent folder and collect the numbers of already-existing subfolders
    # with the same name prefix. p.iterdir() lists the contents; p.is_dir() -> folders only;
    # p.name[len(prefix):] is the suffix after the prefix; .isdigit() checks whether it is purely a number.
    # Note: int("03") == 3, so the same logic works for names with leading zeros.
    indices = [
        int(p.name[len(prefix):])
        for p in parent.iterdir()
        if p.is_dir() and p.name.startswith(prefix) and p.name[len(prefix):].isdigit()
    ]
    i = (max(indices) + 1) if indices else 1          # next free number (or 1 if none)

    # Safeguard loop: should a folder with this number nevertheless exist, increment the number and try again.
    while True:
        # {i:02d} -> number zero-padded to 2 digits (01, 02, ... 99).
        # If you expect more than 99 runs per day for one sample,
        # change 02 to 03 (then 001, 002, ...) so that sorting still works.
        subfolder_name = f"{prefix}{i:02d}"
        folder = parent / subfolder_name              # '/' joins paths in pathlib
        try:
            folder.mkdir()                            # create the folder; error if it already exists
            break                                     # success -> leave the loop
        except FileExistsError:
            i += 1                                    # taken -> try the next number
    return folder, i, today, subfolder_name


def build_plan(steps, width, offset, andor_dir_win, sample_safe, acquisitions_per_gate,
               group_equal_gate_acquisitions):
    """
    Builds the series "plan": one entry (dictionary) per ACQUISITION. The same plan
    drives BOTH the generated Andor program AND the Python sweep - this way they
    cannot diverge in the number of acquisitions or their order.

    For each gate delay we emit `acquisitions_per_gate` entries that share the same
    index/gate_start/C/D but differ in their `count` (1..acquisitions_per_gate) and
    therefore in their filename (_count<N>). One entry == one .sif file == one
    camera trigger.

    The ORDER of the entries depends on `group_equal_gate_acquisitions`:
      * True  -> GROUPED: all repeats of a gate are consecutive
                 (g1c1, g1c2, ..., g2c1, g2c2, ...).
      * False -> INTERLEAVED: one acquisition per gate, the whole sweep repeated
                 `acquisitions_per_gate` times (g1c1, g2c1, ..., g1c2, g2c2, ...).
    `count` means the same thing in both modes (which repeat of THAT gate), so the
    filenames are identical between modes - only the acquisition order differs. This
    lets you separate bleaching (uniform drop loop-to-loop) from genuine gate-setting
    effects (a gate consistently weaker across loops); see the setting's comment.

    Parameters:
    ----------
    steps : list[float]
        List of gate delays relative to the pulse [s] (GATE_DELAY_STEPS).
    width : float
        Gate width [s] (GATE_WIDTH).
    offset : float
        Fixed delay positioning the gate on the pulse [s] (OFFSET_C_DELAY).
    andor_dir_win : str
        Path (Windows style) of the folder into which Andor will save the files.
    sample_safe : str
        Cleaned sample name.
    acquisitions_per_gate : int
        How many acquisitions (files) to take at each gate delay (ACQUISITIONS_PER_GATE).
    group_equal_gate_acquisitions : bool
        Ordering mode (GROUP_EQUAL_GATE_ACQUISITIONS): True = grouped, False = interleaved.

    Returns:
    -------
    list[dict]
        List of entries; each contains: index, count, gate_start, C (gate-start delay
        sent to the DG645), D (gate end), basename (name stem) and fpath (full .sif path).

    Raises:
    ------
    ValueError
        If any step would require a negative gate-start delay (C = gate_start + offset
        < 0), i.e. the gate would start before T0.
    """
    # First pass: validate every gate and pre-compute its C/D once. We do this BEFORE
    # building entries so the C >= 0 guard fires the same way in both ordering modes,
    # and so an interleaved plan never gets half-built before an invalid gate is hit.
    gates = []
    for index, gate_start in enumerate(steps, start=1):
        C = gate_start + offset          # actual gate-START delay sent to the DG645
        # A DG645 channel delay is measured FROM T0 and cannot be negative: the gate
        # cannot start before the device is triggered. C = gate_start + offset, so the
        # earliest physically valid step is gate_start = -offset (C = 0). A more negative
        # step (abs(gate_start) > abs(offset) for a negative step) would demand C < 0,
        # which is impossible - we stop here rather than let the DG645 silently clamp
        # or reject it and shift the whole series.
        if C < 0:
            raise ValueError(
                f"Step {index}: gate_start = {gate_start*1e9:.1f} ns gives C = "
                f"{C*1e9:.1f} ns < 0. A gate cannot start before T0. The most negative "
                f"allowed step is -OFFSET_C_DELAY = {-offset*1e9:.1f} ns "
                f"(i.e. abs(negative step) must be <= abs(offset) = {offset*1e9:.1f} ns)."
            )
        D = C + width                    # gate end (width preserved independently of the offset)
        gates.append((index, gate_start, C, D))

    def _entry(index, gate_start, C, D, count):
        # Build one acquisition entry. basename/fpath depend only on (index, count),
        # so a given gate+repeat yields the same filename regardless of ordering mode.
        basename = make_sif_basename(index, gate_start, width, sample_safe, ACQUISITION_TIME_S, count)
        fpath = andor_dir_win + "\\" + basename + ".sif"   # full file path (backslash = Windows)
        return {
            "index": index, "count": count, "gate_start": gate_start,
            "C": C, "D": D, "basename": basename, "fpath": fpath,
        }

    plan = []
    if group_equal_gate_acquisitions:
        # GROUPED: outer loop over gates, inner loop over repeats of that gate.
        for index, gate_start, C, D in gates:
            for count in range(1, acquisitions_per_gate + 1):
                plan.append(_entry(index, gate_start, C, D, count))
    else:
        # INTERLEAVED: outer loop over repeat number, inner loop over all gates, so
        # the full sweep is taken once per `count` before any gate is revisited.
        for count in range(1, acquisitions_per_gate + 1):
            for index, gate_start, C, D in gates:
                plan.append(_entry(index, gate_start, C, D, count))
    return plan


def build_andor_program(plan, andor_dir_win, sample_raw, comment, today, width, offset, acquisition_time_s,
                        group_equal_gate_acquisitions, power_mw=None):
    """
    Assembles the AndorBasic program text. For each step it writes a separate
    Run() + Save() pair (rather than a loop), with the FULL file path written
    explicitly - this is the clearest, and it shows exactly which file corresponds
    to which delay.

    Parameters:
    ----------
    plan : list[dict]
        The series plan from build_plan().
    andor_dir_win : str
        Output folder path (Windows style).
    sample_raw : str
        The original (uncleaned) sample name - only for the header comment.
    comment : str
        The user's COMMENT text - goes into the header as a record of measurement conditions.
    power_mw : float or None
        Laser power in mW (POWER_MW). Recorded in the header next to the comment.
        None -> "n/a" is written.
    today : str
        The date YYYYMMDD.
    width, offset : float
        Gate width and offset - only for recording in the header (informational).
    acquisition_time_s : float
        The Python acquisition window [s]; the Solis exposure time MUST be <= this value.
    group_equal_gate_acquisitions : bool
        Ordering mode (GROUP_EQUAL_GATE_ACQUISITIONS). Recorded in the header, together
        with the explicit acquisition order, so the temporal sequence is available for
        degradation analysis later (the order is also implicit in the per-file Run/Save
        lines, but is summarised explicitly here for convenience).

    Returns:
    -------
    str
        The whole AndorBasic program as a single text (lines joined by a newline character).
    """
    L = []  # list of lines; at the end we join it into one text
    n_files = len(plan)
    n_gates = len({s["index"] for s in plan})              # distinct gate delays
    per_gate = (n_files // n_gates) if n_gates else 0      # acquisitions per gate
    L.append("// ====================================================================")
    L.append("// AUTO-GENERATED AndorBasic program - do not edit by hand.")
    L.append(f"// Generated by SDG_DG645_gating_integrated.py on {today}")
    L.append("//")
    L.append(f"// Sample         : {sample_raw}")
    L.append(f"// Output folder  : {andor_dir_win}")
    L.append(f"// Gates          : {n_gates}   acquisitions/gate = {per_gate}   total files = {n_files}")
    L.append(f"// Gate Width     : gate width = {width} s   offset_C = {offset} s")
    L.append(f"// Gate offset_C  : offset_C = {offset} s")
    L.append(f"// Solis Exposure : {acquisition_time_s} s")
    # Unique gate delays in measurement order (each appears once even if repeated).
    seen = []
    for s in plan:
        if s["gate_start"] not in seen:
            seen.append(s["gate_start"])
    steps_str = ", ".join(_ns_tag(g) for g in seen) # human-readable ns labels (e.g. "20ns, 50ns")
    L.append(f"// Gate delays    : {steps_str}")
    # --- acquisition ORDER (for degradation analysis) ---------------------------
    # Record both the ordering mode and the explicit temporal sequence of acquisitions.
    # Each token is gNN:cMM -> gate index NN, repeat (count) MM of that gate, in the
    # exact order the camera is triggered. This lets a later analysis reconstruct the
    # time axis and separate degradation (drop across repeats) from gate-setting effects.
    if group_equal_gate_acquisitions:
        order_desc = ("GROUPED (all repeats of a gate taken back-to-back before the "
                      "next gate)")
    else:
        order_desc = ("INTERLEAVED (one frame per gate, the whole sweep repeated "
                      "per count)")
    L.append(f"// Acq. ordering  : {order_desc}")
    order_tokens = [f"g{s['index']:03d}:c{s['count']:03d}" for s in plan]
    # Wrap the token list across several comment lines so no single line is huge.
    PER_LINE = 8
    L.append("// Acq. order     : (file_no -> gate:count, in trigger order)")
    for k in range(0, len(order_tokens), PER_LINE):
        chunk = order_tokens[k:k + PER_LINE]
        # number the first token of each line with its file_no for easy cross-reference
        L.append(f"//   [{k+1:03d}] " + " ".join(chunk))
    L.append("//")
    power_str = f"{power_mw} mW" if power_mw is not None else "n/a"
    L.append(f"// Laser power    : {power_str}")
    L.append("// USER COMMENT:")
    for cline in (comment.splitlines() or [""]):   # comment.splitlines() breaks the multi-line comment into individual lines;
        L.append("//" + cline)
    L.append("// REQUIRED SOLIS SETTINGS:")
    L.append("//    Trigger Mode = External Trigger;  Gater = a 'Fire and Gate' mode;")
    L.append("//    Acquisition mode = Single Acquisition;")
    L.append("//")
    L.append("// Exposure Time needs to be <= ACQUISITION_TIME_S in the Python script,")
    L.append("// It is set in the script automatically (unless rejected by Solis).")
    L.append("// RUN THIS PROGRAM FIRST (it arms and waits for the first trigger),")
    L.append("// then type 'yes' in the Python terminal to start the sweep.")
    L.append("// ====================================================================")
    L.append("")
    L.append(f'print " "') # empty line for readability in Solis output
    L.append(f'print "Sequence: {len(plan)} measurements"')
    L.append(f'Exposure = {acquisition_time_s}')
    L.append(f'SetExposureTime(Exposure)') 
    # Make sure the set exposure is the same as requested. Requesting exposure out of bound would cause Solis 
    # to default to a nearby allowed value. The actual set exposure will be verifiable in the .sif file or converted .ascii 
    # file (in its header).
    for file_no, step in enumerate(plan, start=1):
        L.append("")
        L.append(f'print "Measurement {file_no}/{n_files} (gate {step["index"]}, count {step["count"]}): waiting for trigger"')
        L.append("Run()")                                   # waits for a trigger, takes the exposure
        L.append(f'Save(#0, "{step["fpath"]}")')            # saves the active dataset (#0)
        L.append(f'print "Saved: {step["basename"]}.sif"')
    L.append("")
    L.append('print "Sequence complete."')
    return "\n".join(L)   # "\n".join(list) glues the lines together, inserting a newline between them


def confirm_ready(andor_file_path, acquisition_time_s, n_steps):
    """
    Pauses the program and waits until you have prepared Solis and typed 'yes'. This pause
    serves to (1) load and run the generated Andor program and
    (2) CHECK the settings - especially the exposure time - before the sweep starts.

    Parameters:
    ----------
    andor_file_path : pathlib.Path
        Path to the generated .txt file with the Andor program (to be copied into Solis).
    acquisition_time_s : float
        The Python acquisition window [s]; the Solis exposure time MUST be <= this value.
    n_steps : int
        Number of steps in the series (for display).

    Returns:
    -------
    bool
        True if 'yes' was typed (continue); False if 'no' (abort without measuring).
    """
    # '"=" * 72' creates a string made of 72 "=" characters (i.e. a horizontal separator line).
    # The "\n" at the start is a blank line (move to a new line) for readability.
    print("\n" + "=" * 72)
    print("Andor Basic program generated:")
    print(f"   {andor_file_path}")
    print("\nBefore you type 'yes', do and CHECK the following:")
    print("  1. Open the file above and copy its ENTIRE contents into Andor Solis.")
    print("  2. Check and set the trigger mode = External Trigger and the gate mode 'Fire and Gate' in Solis acquisition settings.")
    print("  3. Run the program in Solis - the camera will arm and wait for a trigger.")
    print("  4. Make sure nothing blocks the beam.")
    print(f"  (The series counts {n_steps} steps / {n_steps} .sif files.)")
    print("=" * 72)
    while True:
        ans = input("Ready? type 'yes' to start, 'no' to abort: ").strip().lower()
        if ans in ("yes", "y", "tak", "t"):
            return True
        if ans in ("no", "n", "nie"):
            return False
        print("   Please type 'yes' or 'no'.")


def cleanup_aborted_run(folder, andor_file):
    """
    On a user abort, offer to delete the run subfolder (and the generated .txt
    Andor program inside it). The folder and .txt are created BEFORE the abort
    decision, so they would otherwise linger as an empty/junk run; this lets the
    operator remove them so the next run keeps a clean sequential index.

    Safety: the sweep never started on an abort, so no .sif files should exist.
    If any .sif files are nevertheless found we REFUSE to delete - that would not
    be an empty aborted run.

    Parameters:
    ----------
    folder : pathlib.Path
        The run subfolder created by make_run_folder().
    andor_file : pathlib.Path
        The generated Andor program .txt inside that folder (informational here;
        it is removed together with the folder).
    """
    while True:
        ans = input("Delete the run folder and the generated .txt? type 'yes'/'no': ").strip().lower()
        if ans in ("no", "n", "nie"):
            print(f"Keeping the folder:\n   {folder}")
            return
        if ans in ("yes", "y", "tak", "t"):
            # Safety check: do not delete if any data (.sif) somehow exists.
            sif_files = list(folder.glob("*.sif"))
            if sif_files:
                print(f"[!] Refusing to delete: {len(sif_files)} .sif file(s) present.")
                print(f"    Folder kept:\n   {folder}")
                return
            try:
                shutil.rmtree(folder)   # removes the folder and everything in it (the .txt)
                print(f"Deleted the run folder and its contents:\n   {folder}")
            except Exception as e:
                print(f"[!] Could not delete the folder: {e}")
                print(f"    Delete it manually if needed:\n   {folder}")
            return
        print("   Please type 'yes' or 'no'.")


# =============================== MAIN ========================================
def main():
    sample_safe = _safe(SAMPLE_NAME)   # cleaned sample name for folders and files

    # 0) Validate the configured steps BEFORE creating any folder, so a bad config
    #    fails fast and leaves no orphaned run folder behind. build_plan() also
    #    enforces C >= 0, but doing it here first keeps the filesystem clean.
    try:
        build_plan(GATE_DELAY_STEPS, GATE_WIDTH, OFFSET_C_DELAY, "", sample_safe, ACQUISITIONS_PER_GATE,
               GROUP_EQUAL_GATE_ACQUISITIONS)
    except ValueError as e:
        print(f"\nConfiguration error - nothing was created:\n   {e}")
        return

    # 1) Create the measurement folder for this run.
    folder, run_i, today, subfolder_name = make_run_folder(DATA_FOLDER, sample_safe, POWER_MW)
    # Windows-style folder path (backslash) - this is what goes into the Andor program.
    # DATA_FOLDER.rstrip("\\/") removes any trailing slash so as not to duplicate the separator.
    andor_dir_win = DATA_FOLDER.rstrip("\\/") + "\\" + subfolder_name
    print(f"Created measurement folder: {folder}")

    # 2) Build a single shared plan, then generate and save the Andor program.
    plan = build_plan(GATE_DELAY_STEPS, GATE_WIDTH, OFFSET_C_DELAY, andor_dir_win, sample_safe, ACQUISITIONS_PER_GATE,
                      GROUP_EQUAL_GATE_ACQUISITIONS)
    andor_text = build_andor_program(plan, andor_dir_win, SAMPLE_NAME, COMMENT,
                                      today, GATE_WIDTH, OFFSET_C_DELAY, ACQUISITION_TIME_S,
                                      GROUP_EQUAL_GATE_ACQUISITIONS, POWER_MW)
    andor_file = folder / f"{subfolder_name}_andor.txt"
    andor_file.write_text(andor_text, encoding="utf-8")   # write the program text to a file
    print(f"Saved Andor program: {andor_file}\n")
    print(f"Series: {len(GATE_DELAY_STEPS)} gates x {ACQUISITIONS_PER_GATE} acquisitions = {len(plan)} files.\n")

    dg645 = None
    try:
        # 3) Connect to the DG645 and set it to an idle state (outputs off) BEFORE
        #    you arm the camera - this way running the Andor program will not catch
        #    an accidental trigger.
        print(f"Connecting to DG645: {DEVICE_IP}...")
        dg645 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        dg645.settimeout(TIMEOUT)
        dg645.connect((DEVICE_IP, PORT))
        send_command(dg645, "*IDN?")                          # identification query
        print(f"Connected to: {dg645.recv(1024).decode().strip()}")  # receive and display the response
        time.sleep(1)

        send_command(dg645, "TSRC 1")   # external triggering on the rising edge (from the SDG)
        send_command(dg645, "BURM 0")   # Burst mode disabled

        # --- CHANNEL DELAYS (why the red "rate" indicator can light up) ---
        # The DG645 cannot start a new delay cycle until the LONGEST programmed delay across
        # ALL eight channels (A-H) has timed out (end-of-cycle). Its max external trigger rate
        # is 1 / (100 ns + longest delay). Any leftover large delay on an UNUSED channel
        # (E,F,G,H) inflates that dead time and can make the DG645 miss SDG triggers at ~1 kHz,
        # so the camera is triggered erratically -> "rate" warning. We pin them all to 0 here.
        # AB (the trigger output) is made an early, short pulse (rising edge at T0) so its
        # delay never dominates the cycle either.
        set_delay(dg645, 'A', 0.0)          # AB rising edge at T0 (camera trigger as early as possible)
        set_delay(dg645, 'B', 100e-9)       # AB falling edge -> a short, clean 100 ns pulse
        for ch in ('G', 'H'):               # unused channels -> delay 0 (do not lengthen the cycle)
            set_delay(dg645, ch, 0.0)

        for out in ('AB', 'CD', 'EF'):        # for both outputs used:
            configure_output_ttl(dg645, out)        # set as a clean TTL (offset 0, positive polarity)
            set_level(dg645, out, OFF_LEVEL_V)      # start with the output OFF (0.5 V)
        print("DG645 configured: external trigger, no burst; A/B = 0/100 ns; G/H = 0; AB/CD/EF outputs off (0.5 V).")

        # 4) Wait until the user loads and runs the Andor program and checks the exposure.
        if not confirm_ready(andor_file, ACQUISITION_TIME_S, len(plan)):
            print("\nAborted by the user. No measurements performed.")
            cleanup_aborted_run(folder, andor_file)   # offer to delete the empty run folder + .txt
            return   # exit main() -> the 'finally' block runs (outputs will be switched off)

        # 5) The sweep. We use the same 'plan' and the same order as the Andor program.
        #    Each plan entry is ONE acquisition (one .sif). Repeats at the same gate
        #    differ only in their count suffix; the gate delay (C/D) is unchanged
        #    between them, so they are re-sent harmlessly.
        print(f"\nStarting sweep: {len(plan)} acquisitions "
              f"({len(GATE_DELAY_STEPS)} gates x {ACQUISITIONS_PER_GATE}).")
        n_files = len(plan)
        per_file_s = TRIGGER_CATCH_S + ACQUISITION_TIME_S + IDLE_TIME_S   # rough per-file time
        for file_no, step in enumerate(plan, start=1):
            remaining_s = per_file_s * (n_files - file_no + 1)           # incl. this file
            print(f"\n========== FILE {file_no}/{n_files} "
                  f"(gate {step['index']}, count {step['count']}): "
                  f"t = {step['gate_start']} s after the pulse  ->  {step['basename']}.sif")
            print(f"   Remaining: ~{remaining_s:.0f} s (~{remaining_s/60:.1f} min)")

            # a) set the gate delays for this step (start C and end D)
            set_delay(dg645, 'C', step['C'])
            set_delay(dg645, 'D', step['D'])
            set_delay(dg645, 'E', step['C'])    # EF mirror: E = C
            set_delay(dg645, 'F', step['D'])    # EF mirror: F = D

            # b) first SWITCH ON the gate (CD), so that no shot is lost
            #    when the exposure begins
            set_level(dg645, 'CD', ACTIVE_LEVEL_V)
            set_level(dg645, 'EF', ACTIVE_LEVEL_V)   # EF mirror
            # c) SWITCH ON the trigger (AB) -> the nearest SDG edge starts the exposure,
            #    and the gate is already running
            set_level(dg645, 'AB', ACTIVE_LEVEL_V)
            # d) wait so the camera definitely catches the edge, then SWITCH OFF the trigger,
            #    so the camera does not start another frame (it will wait in the next Run())
            time.sleep(TRIGGER_CATCH_S)
            set_level(dg645, 'AB', OFF_LEVEL_V)

            # e) keep the gate switched on for the acquisition window (>= the Solis exposure time)
            print(f"   Acquisition {ACQUISITION_TIME_S} s (gate active)...")
            time.sleep(ACQUISITION_TIME_S)

            # f) SWITCH OFF the gate and give Solis time to read out, save and re-arm
            set_level(dg645, 'CD', OFF_LEVEL_V)
            set_level(dg645, 'EF', OFF_LEVEL_V)      # EF mirror
            print(f"   Idle {IDLE_TIME_S} s (readout + save + re-arm)...")
            time.sleep(IDLE_TIME_S)

        print("\n\nSweep complete.")

        # 6) Series check: the number of saved .sif files should equal the number of steps.
        time.sleep(2.0)                          # give Solis a moment to save the last file
        saved = sorted(folder.glob("*.sif"))     # glob("*.sif") finds all .sif files in the folder
        print(f".sif files in the folder: {len(saved)} (expected {len(plan)}).")
        if len(saved) != len(plan):
            print("[!] WARNING: number of files != number of steps. Possible missed/shifted")
            print("    measurement (e.g. IDLE_TIME_S too short). Check the series before analysis.")
        else:
            print("OK: the number of files matches the number of steps.")
        print(f"\nData and Andor program saved in:\n   {folder}\n")

        # 7) MUTE at the end of the series: switch the DG645 into single-shot mode
        #    (TSRC 5). In this mode the device IGNORES external triggers from the SDG, so it does
        #    NOT generate a T0 cycle or any AB/CD pulses between/after acquisitions - it is
        #    effectively muted. Together with the low amplitude (0.5 V) this gives double
        #    protection. To return to operation: set TSRC 1 again (this script does so at startup).
        send_command(dg645, "TSRC 5")
        print("DG645 muted: TSRC 5 (single shot - ignores SDG triggers).")

    except Exception as e:
        print(f"\nCritical error: {e}")
    finally:
        # Regardless of whether it was OK or an error: mute both outputs (low amplitude)
        # AND switch to single shot (TSRC 5), so the DG645 does not respond to SDG
        # triggers and sends no pulses. Then close the connection.
        if dg645:
            try:
                set_level(dg645, 'AB', OFF_LEVEL_V)
                set_level(dg645, 'CD', OFF_LEVEL_V)
                set_level(dg645, 'EF', OFF_LEVEL_V)      # EF mirror
                send_command(dg645, "TSRC 5")   # single shot -> ignores SDG -> no output
            except Exception:
                pass
            dg645.close()


def andor_test():
    """
    Offline dry-run helper: builds the run folder and the AndorBasic program from the
    current settings WITHOUT connecting to the DG645 or acquiring anything. Useful for
    checking the generated folder name, filenames and program text before a real run.

    Takes no arguments and returns nothing; it uses locally overridden DATA_FOLDER and
    SAMPLE_NAME (below) so it never writes into the real measurement folder.
    """
    DATA_FOLDER = r"C:\path\to\mock"   # <-- change this to your mock/test data folder
    SAMPLE_NAME = "mock_sample"        # <-- change this to your mock sample name

    sample_safe = _safe(SAMPLE_NAME)   # cleaned sample name for folders and files

    # 1) Create the measurement folder for this run.
    folder, run_i, today, subfolder_name = make_run_folder(DATA_FOLDER, sample_safe, POWER_MW)
    # Windows-style folder path (backslash) - this is what goes into the Andor program.
    # DATA_FOLDER.rstrip("\\/") removes any trailing slash so as not to duplicate the separator.
    andor_dir_win = DATA_FOLDER.rstrip("\\/") + "\\" + subfolder_name
    print(f"Created measurement folder: {folder}")

    # 2) Build a single shared plan, then generate and save the Andor program.
    plan = build_plan(GATE_DELAY_STEPS, GATE_WIDTH, OFFSET_C_DELAY, andor_dir_win, sample_safe, ACQUISITIONS_PER_GATE,
                      GROUP_EQUAL_GATE_ACQUISITIONS)
    andor_text = build_andor_program(plan, andor_dir_win, SAMPLE_NAME, COMMENT,
                                      today, GATE_WIDTH, OFFSET_C_DELAY, ACQUISITION_TIME_S,
                                      GROUP_EQUAL_GATE_ACQUISITIONS, POWER_MW)
    andor_file = folder / f"{subfolder_name}_andor.txt"
    andor_file.write_text(andor_text, encoding="utf-8")   # write the program text to a file
    print(f"Saved Andor program: {andor_file}\n")
    print(f"Series: {len(GATE_DELAY_STEPS)} gates x {ACQUISITIONS_PER_GATE} acquisitions = {len(plan)} files.\n")




if __name__ == "__main__":
    main()
    #andor_test()