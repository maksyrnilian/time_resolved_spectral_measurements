Device control utilities for DG645 / SDG / iStar

Brief
- Simple scripts to verify and control a DG645 delay generator and to
  coordinate gated-trigger sweeps with an Andor iStar camera.

Files
- `DG645_test_connection.py`: quick TCP/IP check that queries the DG645 (*IDN?) to verify connectivity.
- `SDG_DG645_iSTAR.py`: builds run folders and AndorBasic programs, configures DG645 delays/levels, runs gated-trigger sweeps, and checks saved .sif files. Has main() function for full functionality and andor_test() for simply generating the andor basic code and folders without driving the DG645.

Quick notes
- Default DG645 network: `192.168.1.6:5025` (edit in scripts if needed).
- Configure `DATA_FOLDER`, `SAMPLE_NAME`, and timing constants in `SDG_DG645_iSTAR.py` before use.
- Run `DG645_test_connection.py` first to confirm connectivity.
