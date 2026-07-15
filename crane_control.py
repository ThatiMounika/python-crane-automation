# ==========================================================
# Crane Automation HMI
# ----------------------------------------------------------
# This application provides:
# 1. Manual crane control through a Tkinter HMI.
# 2. Communication with a PLC/Simulator using Modbus TCP.
# 3. Execution of predefined crane sequences stored in JSON.
# 4. Automatic crane operation triggered by digital inputs.
# ==========================================================

import json
import pandas as pd
import tkinter as tk
from tkinter import ttk, messagebox
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException
import time
from datetime import datetime
import threading
import queue
import sys
import os
import subprocess

# Connect to Modbus server
client = ModbusTcpClient('127.0.0.1', port=502)
STEP_SIZES = [10, 5, 1]
current_step = 1  # default step size
JSON_FILE = "crane_commands-2.json"
LOG_FILE  = "logfile.csv"

# Read value from Modbus register
def read_input(addr: int):
    try:
        rr = client.read_holding_registers(addr, count=1)
        if rr.isError():
            return None
        return rr.registers[0]
    except ModbusException:
        return None

# Write value to Modbus register
def write_output(addr: int, value: int):
    try:
        client.write_register(addr, value)
    except ModbusException:
        pass

# Update selected position in JSON file
def overwrite_position_in_json(pid: int, row_index: int, new_x: int, new_y: int):
    with open(JSON_FILE) as f:
        data = json.load(f)
    key = "p1" if pid == 1 else "p2"
    actions = data[key]["actions"]
    if row_index >= len(actions):
        messagebox.showerror("Error", "Row index out of range.")
        return
    actions[row_index]["setX"] = new_x
    actions[row_index]["setY"] = new_y
    with open(JSON_FILE, "w") as f:
        json.dump(data, f, indent=4)

# Save crane movements to CSV log
def log_write(txt: str):
    with open(LOG_FILE, "a") as f:
        f.write(txt)
    print(txt.strip())

# Queue to store incoming process requests
source_queue = queue.Queue()
automation_running = [False]

# Monitor process sensors continuously
def monitor_sensor():
    lastp1 = lastp2 = 0
    while True:
        src1 = read_input(17)
        if src1 == 1 and lastp1 == 0:
            source_queue.put(1)
            print(">>> process1 added to queue")
        if lastp1 != src1:
            lastp1 = src1

        src2 = read_input(18)
        if src2 == 1 and lastp2 == 0:
            source_queue.put(2)
            print(">>> process2 added to queue")
        if lastp2 != src2:
            lastp2 = src2
        time.sleep(0.5)

# Execute crane commands from JSON
def execute_commands_from_json(pid: int):
    with open(JSON_FILE) as f:
        data = json.load(f)
    key = "p1" if pid == 1 else "p2"
     
    actions = data.get(key, {}).get("actions", [])
    df = pd.DataFrame(actions)
    vaccum = 0
    for _, row in df.iterrows():
        if "vacuum" in row and pd.notna(row["vacuum"]):
            write_output(3, int(row["vacuum"]))
            vaccum = int(row["vacuum"])
        if "p2" in row and pd.notna(row["p2"]):
            v = bool(row["p2"])
            write_output(5, v); write_output(20, v); write_output(22, v)
        if "p1" in row and pd.notna(row["p1"]):
            v = bool(row["p1"])
            write_output(4, v); write_output(19, v); write_output(21, v)
        if "t1" in row and pd.notna(row["t1"]):
            t = int(row["t1"])
            print (t)
            time.sleep(t)

        if ("setX" in row and pd.notna(row["setX"])) or ("setY" in row and pd.notna(row["setY"])):
            target_x = int(row["setX"]) if "setX" in row and pd.notna(row["setX"]) else read_input(15)
            target_y = int(row["setY"]) if "setY" in row and pd.notna(row["setY"]) else read_input(16)
            if target_x is None or target_y is None:
                continue
            write_output(1, target_x)
            write_output(2, target_y)
            timestamp = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
            log_write(f"{pid},{timestamp},{target_x},{target_y},{vaccum}\n")

            for _ in range(100):
                cx = read_input(15)
                cy = read_input(16)
                if cx == target_x and cy == target_y:
                    break
                time.sleep(0.1)
            else:
                print(f"Timeout waiting for ({target_x},{target_y})")

# Main automation loop
def automation_loop():
    counter = 0
    print ("x")
    while True:
        if not source_queue.empty():
            counter = 0
            pid = source_queue.get()
            execute_commands_from_json(pid)
        else:
            time.sleep(2)
            counter += 1
            if counter >= 5:
                break
    print("Automation finished / stopped.")

# Crane Human Machine Interface
class CraneHMI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Crane Control HMI – Grade A")
        self.geometry("250x400")
        self.resizable(False, False)

       
        control_frame = ttk.LabelFrame(self, text="Manual Control")
        control_frame.pack(padx=10, pady=10, fill="x")

        step_frame = ttk.Frame(control_frame)
        step_frame.grid(row=0, column=0, columnspan=3, pady=(5, 10))

        ttk.Label(step_frame, text="Step:", font=("Arial", 9)).pack(side="left", padx=2)
        self.step_var = tk.IntVar(value=current_step)
        for sz in STEP_SIZES:
            rb = ttk.Radiobutton(step_frame, text=str(sz), variable=self.step_var,
                                 value=sz, command=lambda s=sz: self.set_step(s))
            rb.pack(side="left", padx=4)

        
        move_frame = ttk.Frame(control_frame)
        move_frame.grid(row=1, column=0, columnspan=3)

        ttk.Button(move_frame, text="-X", width=5,
                   command=lambda:self.move_step(-current_step, 0)).grid(row=1, column=0, padx=5, pady=3)
        ttk.Button(move_frame, text="+X", width=5,
                   command=lambda: self.move_step(current_step, 0)).grid(row=1, column=2, padx=5, pady=3)
        ttk.Button(move_frame, text="-Y", width=5,
                   command=lambda: self.move_step(0, -current_step)).grid(row=2, column=0, padx=5, pady=3)
        ttk.Button(move_frame, text="+Y", width=5,
                   command=lambda: self.move_step(0, current_step)).grid(row=2, column=2, padx=5, pady=3)

        self.lbl_x = ttk.Label(move_frame, text="X: --", font=("Courier", 12), foreground="blue")
        self.lbl_y = ttk.Label(move_frame, text="Y: --", font=("Courier", 12), foreground="blue")
        self.lbl_x.grid(row=1, column=1, padx=10)
        self.lbl_y.grid(row=2, column=1, padx=10)

        list_frame = ttk.LabelFrame(self, text="Saved Positions (select to update)")
        list_frame.pack(padx=10, pady=5, fill="both", expand=True)
        self.listbox = tk.Listbox(list_frame, height=8, exportselection=False, font=("Arial", 9))
        self.listbox.pack(side="left", fill="both", expand=True, padx=(5,0), pady=5)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        scrollbar.pack(side="right", fill="y", pady=5)
        self.listbox.config(yscrollcommand=scrollbar.set)

        action_frame = ttk.Frame(self)
        action_frame.pack(pady=8)
        ttk.Button(action_frame, text="Save-Selected", 
                   command=self.update_selected_with_current).grid(row=1, column=0, padx=8)

        self.run_btn = ttk.Button(action_frame, text="Run-Crane", 
                                  command=self.automation)
        self.run_btn.grid(row=1, column=1, padx=8)

        self.after(200, self.refresh_loop)

   # Set movement step size
    def set_step(self, size):
        global current_step
        current_step = size
        self.step_var.set(size)

    # Move crane manually
    def move_step(self, dx: int, dy: int):
        cur_x = read_input(15) or 0
        cur_y = read_input(16) or 0
        write_output(1, cur_x + dx)
        write_output(2, cur_y + dy)

    # Update displayed crane position
    def refresh_loop(self):
        x = read_input(15)
        y = read_input(16)
        if x is not None:
            self.lbl_x.config(text=f"X: {x}")
        if y is not None:
            self.lbl_y.config(text=f"Y: {y}")

        if getattr(self, "_last_json_hash", None) != self._hash_json():
            self._last_json_hash = self._hash_json()
            self.rebuild_position_list()

        self.after(1000, self.refresh_loop)

    # Check if JSON file has changed
    def _hash_json(self):
        try:
            with open(JSON_FILE, "rb") as f:
                return hash(f.read())
        except Exception:
            return None

    # Load saved positions into the list
    def rebuild_position_list(self):
        self.listbox.delete(0, tk.END)
        with open(JSON_FILE) as f:
            data = json.load(f)
        self._positions = []  

        for pid, key in [(1, "p1"), (2, "p2")]:
            actions = data.get(key, {}).get("actions", [])
            for idx, act in enumerate(actions):
                sx = act.get("setX")
                sy = act.get("setY")
               
                if sx is None or sy is None:
                    continue
                txt = f"P{pid}: X={sx} Y={sy}"
                self.listbox.insert(tk.END, txt)
                self._positions.append((pid, idx, txt))

    # Save current crane position
    def update_selected_with_current(self):
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showinfo("Info", "Select a position from the list first.")
            return
        cur_x = read_input(15)
        cur_y = read_input(16)
        if cur_x is None or cur_y is None:
            messagebox.showerror("Error", "Cannot read current crane position.")
            return
        pid, row_idx, _ = self._positions[sel[0]]
        overwrite_position_in_json(pid, row_idx, cur_x, cur_y)
        messagebox.showinfo("Success", f"Position updated → X={cur_x} Y={cur_y}")
       
    # Start automatic crane operation
    def automation(self):
        
        automation_running[0] = True
      
        global sensor_thread
        if not sensor_thread.is_alive():
            sensor_thread = threading.Thread(target=monitor_sensor, daemon=True)
            sensor_thread.start()
        time.sleep(2)
        automation_loop()

# Program starts here
if __name__ == "__main__":
 SIMULATION_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "simulation.exe"
)

if os.path.exists(SIMULATION_FILE):
    proc = subprocess.Popen([SIMULATION_FILE])
else:
    proc = None
    print("simulation.exe not found. Start the simulation manually.")
    with open(LOG_FILE, "w") as f:
        f.write("Product ID,TimeStamp,X,Y,Vaccum\n")

    if not client.connect():
        print("Could not connect to Modbus server – exiting.")
        sys.exit(1)
    print("Connected to Modbus server")

    sensor_thread = threading.Thread(target=monitor_sensor, daemon=True)

    app = CraneHMI()
    app.mainloop()

    
    client.close()
    if proc is not None:
        proc.terminate()
   
    print("Disconnected from Modbus server")