import os
D=r'C:\Users\Ruanyouyi\OneDrive - Hanze\ESPHome'
for f in ('kitchenradar.yaml','neopixel.yaml'):
    p=os.path.join(D,f)
    raw=open(p,'rb').read().decode(); crlf='\r\n' in raw
    s=raw.replace('\r\n','\n')
    anchor='  fast_connect: true\n'
    assert s.count(anchor)==1 and 'power_save_mode' not in s
    s=s.replace(anchor,anchor+'  power_save_mode: none   # sensing node, no BLE: WiFi must never sleep (2026-10-28)\n')
    open(p,'wb').write((s.replace('\n','\r\n') if crlf else s).encode())
    print('ok',f,crlf)
