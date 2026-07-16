import socket
import time

# =============================================================================
# =============================================================================
#  DG645 MANUAL CONTROL - quick standalone delay/level setter
#  ----------------------------------------------------------
#  Purpose: set DG645 channel delays and output levels WITHOUT going through the
#  full SDG/camera sync procedure. Use this when you only want the DG645 to do
#  one job - e.g. provide a gate to a gated camera that is itself running on its
#  OWN internal trigger (Andor "Internal" trigger mode), so no AB camera-trigger
#  handshake and no .sif filename bookkeeping is needed.
#
#  HOW TO USE
#  ----------
#  1. Edit the USER SETTINGS block below: which channel delays to set, which
#     outputs to turn on/off, and at what amplitude.
#  2. Run this file. It connects, applies your settings, prints what it did,
#     and (by default) leaves the outputs running so the DG645 keeps gating.
#  3. Re-edit and re-run any time to nudge a delay - this is meant to be edited
#     on the go.
#
#  TRIGGERING
#  ----------
#  TRIGGER_SOURCE selects how the DG645 is clocked:
#     1 -> external rising edge (slaved to the SDG / a master clock)
#     0 -> internal rate generator (DG645 free-runs at INTERNAL_RATE_HZ)
#  For a "camera on its own internal trigger, DG645 just makes a gate" setup you
#  usually want the DG645 free-running internally (TRIGGER_SOURCE = 0), or slaved
#  to whatever common clock the camera also sees. Pick to match your wiring.
#
#  VOLTAGES (same caveat as the main sweep script)
#  -----------------------------------------------
#  iStar EXT TRIG and Gate are 74AC TTL, 50 ohm, <=5 V. 4.0 V high / 0.5 V low
#  are correct only if the input is 50 ohm-terminated. Into high-Z the amplitude
#  doubles (~8 V -> damage). Verify on the scope.
# =============================================================================
# =============================================================================


# ============================ USER SETTINGS ==================================
# --- connection ---
DEVICE_IP = '192.168.1.6'
PORT = 5025
TIMEOUT = 5

# --- triggering ---
# 1 = external (slaved to SDG/master), 0 = internal rate generator (free-run).
TRIGGER_SOURCE = 0
# Only used when TRIGGER_SOURCE == 0. Internal trigger rate in Hz.
INTERNAL_RATE_HZ = 3000.0

# --- levels ---
# ACTIVE = logic HIGH (turn channel "on"), OFF = logic LOW (channel "off").
ACTIVE_LEVEL_V = 4.5
OFF_LEVEL_V = 0.5

# --- delays to set this run, in SECONDS, relative to T0 ---
# Edit freely. Comment out / remove any channel you do not want to touch.
# OFFSET_DELAY defines the "0" reference that delays C..H are measured against.
# It is added to every channel from C onward (C, D, E, F, G, H) but NEVER to
# A/B - the AB output must stay at 0 delay. So a DELAYS value of 0 for C means
# "gate start at the OFFSET_DELAY reference"; positive values step later than it,
# negative values earlier. This mirrors OFFSET_C_DELAY in the main sweep script.
OFFSET_DELAY = 5197e-9

# --- delays to set this run, in SECONDS ---
# A/B are ABSOLUTE (relative to T0). C..H are RELATIVE to OFFSET_DELAY (i.e. the
# value below is added on top of OFFSET_DELAY when sent to the DG645).
# Edit freely. Comment out / remove any channel you do not want to touch.
# A/B form one output (AB), C/D -> CD, E/F -> EF, G/H -> GH.
# A channel's pulse is HIGH from its rising-edge channel to its falling-edge
# channel: e.g. CD is high from C to D, so gate width = D - C.
gate_start = 0
gate_end = 300000
DELAYS = {
    'A': 0.0,        # AB rising edge (ABSOLUTE, always 0)
    'B': 100.0e-9,     # AB falling edge -> 100 ns pulse (ABSOLUTE)
    'C': gate_start*1e-9,        # CD gate start, relative to OFFSET_DELAY (0 = on the reference)
    'D': gate_end*1e-9,     # CD gate end     -> 200 ns gate width
    'E': gate_start*1e-9,        # EF mirrors CD by default
    'F': gate_end*1e-9,
    'G': 0.0,        # unused -> keep at 0 (does not lengthen the cycle)
    'H': 0.0,
}

# Channels that the OFFSET_DELAY is applied to (everything except the AB output).
OFFSET_CHANNELS = ('C', 'D', 'E', 'F', 'G', 'H')

# --- which outputs to drive, and to what level ---
# Map output -> amplitude in volts. Use ACTIVE_LEVEL_V to enable, OFF_LEVEL_V to
# disable. Outputs NOT listed here are left untouched.
OUTPUT_LEVELS = {
    'AB': ACTIVE_LEVEL_V,       # camera trigger handshake not needed -> leave off
    'CD': ACTIVE_LEVEL_V,    # gate -> on
    'EF': ACTIVE_LEVEL_V,       # second gate copy -> off unless you need it
    'GH': OFF_LEVEL_V,
}

# Mute behaviour after applying settings:
#   MUTE_ON_EXIT = False -> leave settings RUNNING indefinitely; the script just
#       applies them and disconnects, gate keeps coming out (default).
#   MUTE_ON_EXIT = True  -> hold the settings for HOLD_TIME_S seconds, then MUTE
#       (TSRC 5, outputs low) before disconnecting. Set HOLD_TIME_S = 0 to mute
#       immediately, or None to hold until you press Enter in the terminal.
MUTE_ON_EXIT = False
HOLD_TIME_S = 60.0


# ============================ FIXED MAPS ====================================
# BNC output name -> output number (fixed by the DG645): T0=0, AB=1, CD=2, EF=3, GH=4.
OUTPUT_MAP = {'T0': 0, 'AB': 1, 'CD': 2, 'EF': 3, 'GH': 4}
# Delay-channel letter -> internal delay-channel number.
CHANNEL_MAP = {'A': 2, 'B': 3, 'C': 4, 'D': 5, 'E': 6, 'F': 7, 'G': 8, 'H': 9}


# =========================== FUNCTIONS ===============================
def send_command(s, cmd):
    """
    Sends a single text command to the DG645.

    Parameters:
    ----------
    s : socket.socket
        Active network connection (socket) to the DG645 device.
    cmd : str
        An SCPI-standard command, e.g. "*IDN?" or "TSRC 1". A newline is appended
        automatically, which the DG645 requires to accept the command.
    """
    try:
        s.sendall(f"{cmd}\n".encode())  # .encode() converts text into bytes for the network
        time.sleep(50e-3)               # short pause so as not to flood the buffer
    except Exception as e:
        print(f"Error sending command '{cmd}': {e}")


def validate_delays(delays, offset, offset_channels):
    """
    Checks that every channel delay ACTUALLY SENT to the DG645 is >= 0.

    A DG645 channel delay is measured FROM T0 and cannot be negative: a gate (or
    any edge) cannot be placed before the device is triggered. For the offset
    channels (C..H) the value sent is  applied = value + offset, so a negative
    DELAYS entry is only valid while abs(negative value) <= abs(offset)
    (equivalently applied >= 0). A/B are absolute, so their values must be >= 0
    on their own. Mirrors the C >= 0 guard in build_plan() of the sweep script.

    Parameters:
    ----------
    delays : dict[str, float]
        The DELAYS mapping (channel letter -> seconds).
    offset : float
        OFFSET_DELAY [s], added to the offset channels.
    offset_channels : tuple[str, ...]
        Channels the offset is applied to (OFFSET_CHANNELS).

    Raises:
    ------
    ValueError
        If any channel's applied (absolute, T0-referenced) delay would be < 0.
    """
    for ch, val in delays.items():
        applied = val + offset if ch in offset_channels else val
        if applied < 0:
            if ch in offset_channels:
                raise ValueError(
                    f"Channel {ch}: DELAYS value = {val*1e9:.1f} ns plus OFFSET_DELAY "
                    f"= {offset*1e9:.1f} ns gives an absolute delay of {applied*1e9:.1f} "
                    f"ns < 0. A gate cannot start before T0. The most negative allowed "
                    f"value for an offset channel is -OFFSET_DELAY = {-offset*1e9:.1f} ns "
                    f"(i.e. abs(negative value) must be <= abs(offset) = {offset*1e9:.1f} ns)."
                )
            raise ValueError(
                f"Channel {ch}: absolute delay = {val*1e9:.1f} ns < 0. A/B delays are "
                f"measured directly from T0 and must be >= 0."
            )


def set_delay(s, channel_name, delay_time):
    """
    Sets the delay of a given channel relative to T0 (the DLAY command).

    Parameters:
    ----------
    s : socket.socket
        Active connection to the DG645.
    channel_name : str
        Channel letter: 'A'..'H'.
    delay_time : float
        Delay time in seconds (measured from T0).
    """
    if channel_name in CHANNEL_MAP:
        chan_num = CHANNEL_MAP[channel_name]
        # Command format: DLAY <channel>,<reference_channel=0(T0)>,<time in s>
        send_command(s, f"DLAY {chan_num},0,{delay_time}")
        print(f"   -> Channel {channel_name} = {delay_time} s")
    else:
        print(f"   [!] Unknown channel: {channel_name}")


def set_level(s, output_name, amplitude_v):
    """
    Sets the AMPLITUDE of a given DG645 output (the LAMP command), used here as a
    software on/off switch (high amplitude = logic HIGH = "on").

    Parameters:
    ----------
    s : socket.socket
        Active connection to the DG645.
    output_name : str
        BNC output name: 'T0', 'AB', 'CD', 'EF' or 'GH'.
    amplitude_v : float
        Amplitude in volts (DG645 allowed range: 0.5 - 5.0 V).
    """
    if output_name in OUTPUT_MAP:
        b = OUTPUT_MAP[output_name]
        send_command(s, f"LAMP {b},{amplitude_v}")
        state = "ON " if amplitude_v >= ACTIVE_LEVEL_V else "off"
        print(f"   -> Output {output_name}: {amplitude_v} V  [{state}]")
    else:
        print(f"   [!] Unknown output: {output_name}")


def configure_output_ttl(s, output_name):
    """
    Configures a DG645 output as a clean TTL signal: a square pulse from 0 V (low)
    up to the set amplitude (high). Sets LOFF (offset) = 0 V and LPOL (polarity) = 1
    (positive, pulse goes up). The amplitude itself is set separately via set_level().

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


def connect(ip, port, timeout):
    """
    Opens a TCP connection to the DG645 and prints its identification string.

    Returns:
    -------
    socket.socket
        The connected socket.
    """
    print(f"Connecting to DG645: {ip}...")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect((ip, port))
    send_command(s, "*IDN?")
    print(f"Connected to: {s.recv(1024).decode().strip()}")
    return s


# =============================== MAIN ========================================
def main():
    # Validate the configured delays BEFORE connecting, so a bad config fails
    # fast with a clear message and never touches the instrument.
    try:
        validate_delays(DELAYS, OFFSET_DELAY, OFFSET_CHANNELS)
    except ValueError as e:
        print(f"\nConfiguration error - nothing was sent to the DG645:\n   {e}")
        return

    dg645 = None
    try:
        dg645 = connect(DEVICE_IP, PORT, TIMEOUT)
        time.sleep(1)

        # --- trigger source ---
        if TRIGGER_SOURCE == 0:
            send_command(dg645, "TSRC 0")                 # internal rate generator
            send_command(dg645, f"TRAT {INTERNAL_RATE_HZ}")
            print(f"Trigger: internal rate generator at {INTERNAL_RATE_HZ} Hz.")
        else:
            send_command(dg645, "TSRC 1")                 # external rising edge
            print("Trigger: external rising edge (slaved to master clock).")
        send_command(dg645, "BURM 0")                     # burst mode disabled

        # --- delays ---
        # A/B are absolute; C..H are shifted by OFFSET_DELAY (the "0" reference).
        print("Setting channel delays:")
        for ch, val in DELAYS.items():
            applied = val + OFFSET_DELAY if ch in OFFSET_CHANNELS else val
            set_delay(dg645, ch, applied)

        # --- outputs: configure as clean TTL, then apply requested level ---
        print("Setting output levels:")
        for out, level in OUTPUT_LEVELS.items():
            configure_output_ttl(dg645, out)
            set_level(dg645, out, level)

        print("\nDG645 settings applied.")

        if MUTE_ON_EXIT:
            if HOLD_TIME_S is None:
                input("Holding settings - press Enter to mute and exit...")
            elif HOLD_TIME_S > 0:
                print(f"Holding settings for {HOLD_TIME_S} s before muting...")
                time.sleep(HOLD_TIME_S)
            for out in OUTPUT_LEVELS:
                set_level(dg645, out, OFF_LEVEL_V)
            send_command(dg645, "TSRC 5")                 # single shot -> ignores triggers
            print("DG645 muted on exit: TSRC 5, outputs low.")
        else:
            print("Leaving outputs RUNNING (MUTE_ON_EXIT = False).")

    except Exception as e:
        print(f"\nCritical error: {e}")
    finally:
        if dg645:
            dg645.close()


if __name__ == "__main__":
    main()