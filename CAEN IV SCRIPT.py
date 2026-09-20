
import caen_libs
import matplotlib as plt 
import csv
import numpy as np 
from caen_libs import caenhvwrapper as hv
import time 
from datetime import datetime
import pandas as pd
import os

systemtype=hv.SystemType.DT55XXE
linktype = hv.LinkType.USB_VCP
arg = "COM11"
SLOT, CH = 0, 2 #channels on this device go 0, 1, 2 3. 
V_START=0
V_STEP=0.5
MAX_V= 30 #in V
MAX_I= 10 #compliance in uA
LIMIT_I = MAX_I*0.90
SLEEP=1
SLEEP_END=2

try:
    with hv.Device.open(systemtype, linktype, arg) as device, \
        open(f'hv_wrapper.csv', 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['Timestamp', 'VSet', 'VMon', 'IMon', 'ChStatus'])
        print("Connection successful. Device identified as DT5519EM. ")

        device.set_ch_param(SLOT, [CH], "VSet", 0)
        device.set_ch_param(SLOT, [CH], "ISet", MAX_I )
        device.set_ch_param(SLOT, [CH], "Trip", 2.0)
        device.set_ch_param(SLOT, [CH], "ImonRange", 1) # Set IMonRange = 1 for small MAX_I values and 0 for larger MAX_I Values
        device.set_ch_param(SLOT, [CH], "RUp", 10.0)
        device.set_ch_param(SLOT, [CH], "RDwn", 10.0)
        device.set_ch_param(SLOT, [CH], "Pw", 1) 
    
        time.sleep(SLEEP) 
        print("System on.")

        v_steps= np.arange(V_START, MAX_V, V_STEP)
        sweep_active= True
    
        try:
            for v_target in v_steps:
                if not sweep_active: break
                device.set_ch_param(SLOT, [CH], "VSet", v_target)
                time.sleep(SLEEP)

                while True:
                    v_mon, = device.get_ch_param(SLOT, [CH], "VMon")
                    i_mon, = device.get_ch_param(SLOT, [CH], "IMon")
                    status,= device.get_ch_param(SLOT, [CH], "ChStatus")

                    if not (status& 0x1):
                        print("Hardware has turned off.")
                        sweep_active=False
                        break

                    if abs(v_mon - v_target) < 0.15: 
                        break

                    if i_mon > LIMIT_I:
                        print('Exceeding maximum current. Powering off.')
                        device.set_ch_param(SLOT, [CH], "Pw", 0)
                        break
                
                if not sweep_active: break

                timestamp=datetime.now().strftime("%H_%M_%S")
                writer.writerow([timestamp, v_target, v_mon, i_mon, status])
                f.flush() 
                print(f"[{timestamp}] Set: {v_target}V | Mon: {v_mon:.3f}V  | I: {i_mon:.5f}uA")
    
        except KeyboardInterrupt:
            print("\nMonitoring stopped. Powering off.") 
        
        print("Sweep complete. Starting shutdown.")
        device.set_ch_param(SLOT, [CH], "VSet", 0.0)
        time.sleep(SLEEP_END)
        device.set_ch_param(SLOT, [CH], "Pw", 0)
        device.exec_comm("ClearAlarm")


finally:
    try: 
        device.set_ch_param(SLOT, [CH], "VSet", 0.0)
        time.sleep(SLEEP_END)
        device.set_ch_param(SLOT, [CH], "Pw", 0)

    except Exception as e:
        if "NOTCONNECTED" in str(e):
            print("Shutdown complete.")

df=pd.read_csv('hv_wrapper.csv')
counter =1 
while os.path.exists(f"hv_wrapper_{counter}.csv"):
    counter+=1
df.to_csv(f"hv_wrapper_{counter}.csv", index=False)

