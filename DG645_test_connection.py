import socket

DEVICE_IP = '192.168.1.6' # Needs to match the IP address set on the DG645 front panel
PORT = 5025
TIMEOUT = 5

try:
    print(f"Attempting to connect to {DEVICE_IP}...")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(TIMEOUT) 
   
    s.connect((DEVICE_IP, PORT))  # Connect
    s.sendall(b"*IDN?\n") # Send the ID query
    
    response = s.recv(1024).decode().strip() # Read the response
    
    print("\nSUCCESS! Connection Verified.")
    print(f"Device responded: {response}")

except socket.timeout:
    print("\nERROR: Connection timed out. The IP is likely correct (if ping worked), but the device isn't responding on Port 5025.")
except ConnectionRefusedError:
    print("\nERROR: Connection refused. The device is reachable, but it is not accepting connections (Port 5025 might be closed).")
except Exception as e:
    print(f"\nERROR: {e}")
finally:
    s.close()