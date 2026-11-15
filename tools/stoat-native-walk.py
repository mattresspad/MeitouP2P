"""Native-input acceptance trial for the two visible Stoat windows on stock Windows Meitou."""
import argparse
import ctypes
from ctypes import wintypes
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import itertools
import json
import math
from pathlib import Path
import time


class Client:
    def __init__(self, run, side, evidence):
        self.side = side
        self.folder = run / side
        self.control = self.folder / 'control'
        self.evidence = evidence
        self.pid = int((self.folder / 'client.pid').read_text())
        self.serial = itertools.count(time.time_ns() // 1000000)
        self.trace = evidence / (side + '.jsonl')

    def ask(self, *commands):
        ids = [next(self.serial) for cmd in commands]
        out = self.control / 'out'
        offset = out.stat().st_size if out.exists() else 0
        inbox = self.control / 'cmd'
        if inbox.exists():
            raise RuntimeError(f'{self.side} control channel is busy')
        temp = self.control / 'native-walk.tmp'
        temp.write_text(''.join(f'{i} {s}\n' for i,s in zip(ids,commands)), encoding='utf-8')
        temp.replace(inbox)
        limit = time.monotonic() + 18
        found = {}
        while len(found) != len(ids):
            if time.monotonic() > limit:
                raise TimeoutError((self.side, commands, found))
            if out.exists():
                with out.open('rb') as f:
                    f.seek(offset)
                    for line in f:
                        if not line.endswith(b'\n'):
                            break
                        offset += len(line)
                        row = json.loads(line)
                        key = row.get('id')
                        if key in ids:
                            if not row.get('ok'):
                                raise RuntimeError((self.side, commands[ids.index(key)], row))
                            found[key] = row['data']
            time.sleep(.025)
        with self.trace.open('a',encoding='utf-8') as f:
            for i,cmd in zip(ids,commands):
                f.write(json.dumps(dict(command=cmd, result=found[i], time=time.time()))+'\n')
        return [found[i] for i in ids]

    def one(self, cmd):
        return self.ask(cmd)[0]

    def click(self, point, button='left'):
        return self.one(f'click {point[0]} {point[1]} --button {button}')

    def body(self, who):
        actor = self.actors()[who]
        p = self.one('entity-screen ' + actor['id'])['center']
        hit = self.one(f'world pick {p[0]} {p[1]}')
        assert hit.get('id') == actor['id'], (who,p,hit)
        return p

    def actors(self):
        rows = self.one('world --near 34665 -70142 --radius 650 --limit 100')
        return {r['name']:r for r in rows if r['name'] in (SCOUT,GUIDE)}

    def select(self, point, expected):
        self.click(point)
        for attempt in range(8):
            selected = [r['name'] for r in self.actors().values() if r['selected']]
            if selected == expected:
                return
        raise AssertionError((self.side, point, selected, expected))

    def ground(self, point):
        hit = self.one(f'world pick {point[0]} {point[1]}')
        assert hit['kind']=='terrain', (self.side, point, hit)
        return hit['terrain']

    def window(self):
        ui = ctypes.WinDLL('user32', use_last_error=True)
        handles = []
        callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        @callback
        def find(hwnd, data):
            pid = wintypes.DWORD()
            ui.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == self.pid and ui.IsWindowVisible(hwnd):
                title = ctypes.create_unicode_buffer(256)
                ui.GetWindowTextW(hwnd, title, len(title))
                if 'MeitouClient' in title.value:
                    handles.append(hwnd)
            return True
        ui.EnumWindows(find, 0)
        assert len(handles)==1, (self.pid, handles)
        return wintypes.HWND(handles[0])

    def pan(self, key, seconds):
        # Send only to the guest process's visible game window, including when the host has focus.
        ui = ctypes.WinDLL('user32', use_last_error=True)
        vk = ord(key)
        scan = ui.MapVirtualKeyW(vk, 0)
        hwnd = self.window()
        message = 1 | (scan << 16)
        if not ui.PostMessageW(hwnd, 0x100, vk, message):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            time.sleep(seconds)
        finally:
            if not ui.PostMessageW(hwnd, 0x101, vk, message | (3<<30)):
                raise ctypes.WinError(ctypes.get_last_error())


SCOUT, GUIDE = 'Stoat Scout','Stoat Guide'
def distance(a,b):
    return math.hypot(a[0]-b[0],a[2]-b[2])


def projected_body(position, camera, size):
    # Test camera faces along -Z (yaw 0); vertical field of view is in degrees.
    x,y,z = [a-b for a,b in zip(position,camera['eye'])]
    y += 10
    pitch = math.radians(camera['pitch'])
    depth = -y*math.sin(pitch)-z*math.cos(pitch)
    up = y*math.cos(pitch)-z*math.sin(pitch)
    f = size[1]/(2*math.tan(math.radians(camera['fov']/2)))
    return [round(size[0]/2+f*x/depth),round(size[1]/2-f*up/depth)]


def run_trial(host, guest, evidence):
    pool = ThreadPoolExecutor(2)
    clients = (host,guest)
    ui=ctypes.WinDLL('user32',use_last_error=True)
    ui.SetProcessDPIAware()
    area=wintypes.RECT()
    if not ui.SystemParametersInfoW(0x30,0,ctypes.byref(area),0):
        raise ctypes.WinError(ctypes.get_last_error())
    width=min(876,(area.right-area.left)//2)
    height=min(640,area.bottom-area.top)
    for i,c in enumerate(clients):
        x=(area.left+area.right-2*width)//2 + i*width
        y=(area.top+area.bottom-height)//2
        if not ui.SetWindowPos(c.window(),0,x,y,width,height,20):
            raise ctypes.WinError(ctypes.get_last_error())
        deadline=time.monotonic()+240
        while c.one('state')['screen']!='world':
            if time.monotonic()>deadline:
                raise TimeoutError(c.side+' world load')
            time.sleep(1)
    host.click([62,55])
    time.sleep(.45)
    guest.click([62,55])
    time.sleep(1)
    index = 0
    records = []
    def capture(label):
        nonlocal index
        def side(c):
            pic=evidence/f'{index:02d}-{c.side}.png'
            state,cam,rows,_ = c.ask('state','camera','world --near 34665 -70142 --radius 650 --limit 75',f'screenshot "{pic}"')
            actors={r['name']:{k:r.get(k) for k in ('position','selected','onScreen','screen')} for r in rows if r['name'] in (SCOUT,GUIDE)}
            assert set(actors)=={SCOUT,GUIDE}, actors
            return dict(tick=state['tick'],camera=cam,actors=actors,image=pic.name)
        h,g = pool.map(side,clients)
        item=dict(label=label,host=h,guest=g,time=time.time())
        records.append(item)
        (evidence/'stages.json').write_text(json.dumps(records,indent=2))
        print(index,label,[(k, v['position'],v['selected']) for k,v in g['actors'].items()],flush=True)
        index+=1
        return item

    cam='camera set --pos 34665 -70142 --yaw 0 --pitch 25 --zoom 225'
    list(pool.map(lambda c:c.one(cam),clients))
    time.sleep(.7)
    # Two releases of the stock speed arrow take the Scout from Free through Jog to Walk.
    host.select(host.body(SCOUT),[SCOUT])
    tree=host.one('ui-tree')
    rect=next(w['rect'] for w in tree if w['label']=='SpeedArrowLeft')
    arrow=[rect[0]+rect[2]//2,rect[1]+rect[3]//2]
    for _ in range(2):
        host.click(arrow)
        time.sleep(.2)
    empty=[480,155]
    guest.ground(empty)
    guest.select(empty,[])
    old_pixel=guest.body(SCOUT)
    guest.select(old_pixel,[SCOUT])
    baseline=capture('Guest selects Scout by her body')
    old=baseline['guest']['actors'][SCOUT]['position']
    old_guide=baseline['host']['actors'][GUIDE]['position']
    camera=baseline['guest']['camera']
    host_camera=baseline['host']['camera']
    guide_pixel=host.body(GUIDE)
    first=[737,288]
    other=[384,166]
    first_target=guest.ground(first)
    guide_target=host.ground(other)
    assert distance(old,first_target)>100 and distance(old_guide,guide_target)>30
    host_log = host.folder / 'client.log'
    movement_offset = host_log.stat().st_size
    guest.click(first,'right')
    host.select(guide_pixel,[GUIDE])
    host.click(other,'right')
    for _ in range(8):
        frame=capture('Both players move their Wanderer')
        if all(distance(frame[s]['actors'][SCOUT]['position'], old)>1 and
               distance(frame[s]['actors'][GUIDE]['position'], old_guide)>1 for s in ('guest','host')):
            break
    for side in ('guest','host'):
        poses=frame[side]['actors']
        assert distance(poses[SCOUT]['position'],old)>1 and distance(poses[GUIDE]['position'],old_guide)>1, poses
        assert poses[SCOUT]['selected']==(side=='guest') and poses[GUIDE]['selected']==(side=='host')
    assert distance(frame['host']['actors'][SCOUT]['position'],first_target)>5, 'Scout arrived before camera test'
    # Pan away using the game's ordinary D key, clearing selection on empty terrain there.
    for _ in range(6):
        guest.pan('D',.65)
        if not guest.one('entity-screen c:0.0')['onScreen'] and guest.one('camera')['pivot'][0]>first_target[0]+240:
            break
    away=capture('Guest pans away')
    assert not away['guest']['actors'][SCOUT]['onScreen'],away
    guest.ground(empty)
    guest.select(empty,[])
    for _ in range(36):
        frame=capture('Guest looks away while the host resolves the walk')
        if all(distance(frame[s]['actors'][SCOUT]['position'],first_target)<4 and distance(frame[s]['actors'][GUIDE]['position'],guide_target)<4 for s in ('host','guest')):
            break
        time.sleep(.3)
    else:
        raise AssertionError(('arrival',first_target,guide_target,frame))
    assert not frame['guest']['actors'][SCOUT]['onScreen']
    arrival=frame['host']['actors'][SCOUT]['position']
    # Pan back by hand-key input, using the camera pivot (never follow/select an actor).
    for _ in range(22):
        x=guest.one('camera')['pivot'][0]
        delta=x-camera['pivot'][0]
        if abs(delta)<12:
            break
        guest.pan('A' if delta>0 else 'D',min(.55,max(.035,abs(delta)/800)))
        time.sleep(.12)
    guest_camera=guest.one('camera')
    assert abs(guest_camera['pivot'][0]-camera['pivot'][0])<45 and abs(guest_camera['pivot'][2]-camera['pivot'][2])<10,guest_camera
    # Reproject the original body because free panning seldom returns to the exact pixel.
    origin_pixel=projected_body(old,guest_camera,(860,601))
    guest.ground(origin_pixel)
    guest.select(origin_pixel,[])
    capture('Old body location misses')
    guest.select(guest.body(SCOUT),[SCOUT])
    frame=capture('Guest reselects Scout at arrival')
    assert distance(frame['guest']['actors'][SCOUT]['position'],arrival)<4
    second=[490,318]
    second_target=guest.ground(second)
    assert distance(arrival,second_target)>30
    guest.click(second,'right')
    for _ in range(10):
        frame=capture('Guest orders Scout again')
        if all(distance(frame[s]['actors'][SCOUT]['position'],arrival)>4 for s in ('host','guest')):
            break
    else:
        raise AssertionError('Second move did not reach both windows')
    guest.select(guest.body(GUIDE),[GUIDE])
    offset=host_log.stat().st_size
    guest_log = guest.folder / 'client.log'
    feedback_offset = guest_log.stat().st_size
    guest.ground(empty)
    guest.click(empty,'right')
    time.sleep(.4)
    frame=capture('Guest Guide order refused')
    assert b'Only Stoat Scout can take guest orders.' in guest_log.read_bytes()[feedback_offset:]
    guest.one('select --squad')
    for _ in range(8):
        if all(r['selected'] for r in guest.actors().values()):
            break
    else:
        raise AssertionError('Mixed selection setup failed')
    guest.click(empty,'right')
    time.sleep(.4)
    capture('Guest mixed-selection order refused')
    assert b'Guest move submitted' not in host_log.read_bytes()[offset:]
    for s in ('host','guest'):
        assert distance(frame[s]['actors'][GUIDE]['position'],guide_target)<4
    assert all(r['host']['actors'][GUIDE]['selected'] for r in records[1:])
    assert all(distance(r['host']['camera']['pivot'],host_camera['pivot'])<1 for r in records)
    # Finish with the Scout selected and both peers ready for a manual replay.
    for _ in range(40):
        poses=guest.actors()
        if distance(poses[SCOUT]['position'],second_target)<4:
            break
        time.sleep(.5)
    else:
        raise AssertionError(('second arrival',second_target,poses))
    guest.select(guest.body(SCOUT),[SCOUT])
    capture('Ready to play again')
    orders = [s for s in host_log.read_bytes()[movement_offset:].splitlines() if b'Guest move submitted' in s]
    assert len(orders) == 2, orders
    print('PASS', evidence,flush=True)
    pool.shutdown()


if __name__=='__main__':
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('run',type=Path,help='fresh visible run from stoat-windows.ps1, with the game control channels ready')
    args=cli.parse_args()
    run=args.run.resolve()
    evidence=run/('native-walk-'+datetime.now().strftime('%H%M%S-%f'))
    evidence.mkdir()
    run_trial(Client(run,'host',evidence),Client(run,'guest',evidence),evidence)
