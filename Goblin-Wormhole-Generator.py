# -*- coding: utf-8 -*-
"""
泰坦装置 TITAN DEVICE
AzerothSurvivors 中国大陆低延迟联机启动器
原理：Radmin LAN 虚拟组网 + 游戏本地 TCP 直连（绕过官方境外中继）
"""
import os
import sys
import re
import json
import time
import socket
import subprocess
import webbrowser
import webview

APP_NAME = "地精虫洞发生器"
VERSION = "1.1.0"

DEFAULT_EXE = r"C:\Users\Huan\Desktop\AzerothSurvivors\AzerothSurvivors.exe"
RADMIN_HOME = "https://www.radmin-lan.cn/"
RADMIN_DL = ("https://pc-lsw7.lenovomm.cn/dlserver/fileman/pcstore/ali/pcsd-pcmgr-appstore/appstore/"
             "normal/apps/6650-2025-11-21060554-1763719554972.exe?sign=5390802827422afaf9c0fd042c95aef7"
             "&t=6ae5f746&order=0&uuid=17143196d19a41f08376afc8e3c10305&cMD5=false&sorder=0&group="
             "&ts=1793283142427&cpn=-1&cid=8848&__bc=10101&__cid=8848&__ip=0.0.0.0&__ept=1&dck=1")


def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_PATH = os.path.join(app_dir(), "GWG.json")

# 让 ipconfig 等控制台子进程不弹出黑色窗口（windowed 打包下会闪一下）
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def resource_path(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def load_icon_b64():
    try:
        with open(resource_path("icon_b64.txt"), "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


class Api:
    """暴露给前端 JS 的后端接口"""

    def __init__(self):
        self._window = None

    def bind(self, window):
        self._window = window

    # ---------- 配置存取 ----------
    def load_config(self):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def save_config(self, cfg):
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False

    # ---------- 游戏路径 ----------
    def default_exe(self):
        return DEFAULT_EXE if os.path.isfile(DEFAULT_EXE) else ""

    def pick_game(self):
        if not self._window:
            return None
        start_dir = os.path.dirname(DEFAULT_EXE) if os.path.isfile(DEFAULT_EXE) else "C:\\"
        res = self._window.create_file_dialog(
            webview.OPEN_DIALOG, directory=start_dir, file_types=("可执行文件 (*.exe)",)
        )
        return res[0] if res else None

    def check_path(self, path):
        if not path:
            return {"ok": False, "msg": "还没有选择文件"}
        ok = os.path.isfile(path)
        name = os.path.basename(path)
        return {
            "ok": ok,
            "msg": ("已就位：" + name) if ok else "文件不存在，请重新选择",
            "warn": (not name.lower().startswith("azerothsurvivors")),
            "dir": os.path.dirname(path) if ok else "",
        }

    # ---------- 网络 ----------
    @staticmethod
    def _is_host_ip(ip):
        """过滤子网掩码、网关、广播地址，只留真正的主机地址"""
        parts = ip.split(".")
        if len(parts) != 4 or not all(p.isdigit() and int(p) < 256 for p in parts):
            return False
        if ip.startswith("255.") or ip.startswith("0.") or ip == "127.0.0.1":
            return False
        if ip.endswith(".0.0.1") or ip.endswith(".255") or ip.endswith(".0"):
            return False
        return True

    def detect_ips(self):
        """返回本机所有 IPv4，以及 Radmin VPN 常用的 26.* 段地址"""
        try:
            out = subprocess.run(["ipconfig"], capture_output=True, timeout=15,
                                 creationflags=CREATE_NO_WINDOW)
            txt = out.stdout.decode("gbk", errors="replace")
        except Exception:
            return {"radmin": [], "all": []}
        ips = re.findall(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})(?:\(首选\))?", txt)
        ipv4 = []
        for i in ips:
            if not self._is_host_ip(i):
                continue
            if i not in ipv4:
                ipv4.append(i)
        radmin = [i for i in ipv4 if i.startswith("26.")]
        seen = []
        for i in radmin + ipv4:
            if i not in seen:
                seen.append(i)
        return {"radmin": radmin, "all": seen}

    def test_peer(self, ip, port):
        ip = (ip or "").strip()
        if not ip:
            return {"ok": False, "msg": "先填写主机 IP"}
        try:
            t0 = time.time()
            s = socket.create_connection((ip, int(port)), timeout=3)
            cost = (time.time() - t0) * 1000
            s.close()
            return {"ok": True, "msg": "主机在线，端口已开放（%.0f ms）" % cost}
        except Exception as e:
            kind = type(e).__name__
            hint = {
                "timeout": "连接超时：主机没开房、IP 填错，或 Radmin 没连上",
                "TimeoutError": "连接超时：主机没开房、IP 填错，或 Radmin 没连上",
                "ConnectionRefusedError": "主机在线但端口不通：房主没启动开房模式（窗口开着才算）",
                "gaierror": "IP 格式不对",
                "OSError": "网络不可达，检查 Radmin 是否已加入同一网络",
            }.get(kind, kind)
            return {"ok": False, "msg": "连不上 — " + hint}

    # ---------- 启动游戏 ----------
    def launch(self, cfg):
        exe = cfg.get("game", "")
        if not os.path.isfile(exe):
            return {"ok": False, "msg": "游戏路径无效：" + exe}
        env = os.environ.copy()
        env["WOW_ROGUE_UPDATE_AUTO"] = "0"
        if cfg.get("mode") == "host":
            env["WOW_ROGUE_COOP_HOST"] = str(cfg.get("port", 7777))
        else:
            env["WOW_ROGUE_COOP_JOIN"] = "%s:%s" % (cfg.get("host_ip", "").strip(), cfg.get("port", 7777))
        env["WOW_ROGUE_COOP_PASSWORD"] = cfg.get("password", "mima")
        cwd = os.path.dirname(exe)
        try:
            subprocess.Popen([exe], env=env, cwd=cwd, close_fds=True)
            return {"ok": True, "msg": "游戏已启动 —— 本程序可以关闭了，保持游戏窗口开启即可联机"}
        except Exception as e:
            return {"ok": False, "msg": "启动失败：" + str(e)}

    # ---------- 杂项 ----------
    def open_url(self, url):
        webbrowser.open(url)
        return True

    def app_info(self):
        return {"name": APP_NAME, "version": VERSION}


HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>
  :root{
    --bg:#080b10; --card:#111a24; --card2:#17222f; --line:#273444;
    --gold:#d4a857; --gold2:#f0d391; --blue:#5aa9e6; --teal:#3ecf9a;
    --ok:#3ecf9a; --warn:#d29922; --err:#f85149;
    --txt:#e8eef5; --dim:#8b98a9;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{
    background:radial-gradient(1100px 560px at 50% -12%,#14202e 0%,var(--bg) 62%);
    color:var(--txt); font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif;
    min-height:100vh; padding:26px 30px 28px; user-select:none; font-size:14px;
    overflow-y:auto;
  }
  .brand{display:flex;align-items:center;gap:14px;margin-bottom:6px}
  .brand #logo{width:54px;height:54px;border-radius:13px;border:1px solid rgba(212,168,87,.5);
    box-shadow:0 5px 18px rgba(0,0,0,.5),0 0 0 3px rgba(212,168,87,.08);object-fit:cover;flex:0 0 auto}
  .brand .bt{display:flex;flex-direction:column;gap:2px}
  .brand h1{font-size:26px;letter-spacing:2px;line-height:1.12;
    background:linear-gradient(90deg,var(--gold),var(--gold2));-webkit-background-clip:text;
    background-clip:text;color:transparent;font-weight:700}
  .brand .en{font-size:10px;color:var(--teal);letter-spacing:3px;opacity:.85}
  .sub{color:var(--dim);font-size:12.5px;margin-bottom:18px}

  .steps{display:flex;align-items:center;gap:8px;margin-bottom:20px}
  .steps .s{display:flex;align-items:center;gap:8px;color:var(--dim);font-size:12.5px;transition:.25s}
  .steps .dot{width:28px;height:28px;border-radius:50%;border:1px solid var(--line);
    display:flex;align-items:center;justify-content:center;font-size:12px;transition:.25s;
    background:#0f1620}
  .steps .s.on .dot{background:linear-gradient(135deg,var(--gold),var(--gold2));color:#20180a;
    border-color:var(--gold);box-shadow:0 0 14px rgba(212,168,87,.5);font-weight:700;
    animation:pulse 2.2s ease-out infinite}
  .steps .s.on{color:var(--gold2);font-weight:700}
  .steps .s.done .dot{background:rgba(63,185,80,.16);border-color:var(--ok);color:var(--ok);
    font-weight:700}
  .steps .ln{flex:1;height:2px;background:linear-gradient(90deg,var(--line),#1b2432)}

  .card{background:linear-gradient(160deg,var(--card),var(--card2));
    border:1px solid var(--line);border-radius:16px;padding:24px 26px;margin-bottom:14px;
    box-shadow:0 8px 26px rgba(0,0,0,.32);animation:fadein .26s ease}
  .card h2{font-size:16.5px;margin-bottom:9px;color:var(--gold2);letter-spacing:1px;
    padding-left:12px;border-left:3px solid var(--gold);line-height:1.3}
  .card p{font-size:13.2px;color:var(--dim);line-height:1.85}
  .card p b{color:var(--txt)}

  .row{display:flex;gap:10px;align-items:center;margin-top:14px;flex-wrap:wrap}
  input[type=text]{flex:1;min-width:200px;background:#0c1219;border:1px solid var(--line);
    border-radius:10px;padding:11px 14px;color:var(--txt);font-size:14px;outline:none;transition:.2s;
    font-family:inherit}
  input[type=text]:focus{border-color:var(--gold);box-shadow:0 0 0 4px rgba(200,164,92,.13)}
  input.small{flex:0 0 120px;min-width:96px;text-align:center}

  button{background:linear-gradient(135deg,var(--gold),var(--gold2));color:#20180a;border:none;
    border-radius:10px;padding:11px 20px;font-size:13.5px;font-weight:700;cursor:pointer;transition:.2s;
    font-family:inherit;letter-spacing:.5px;box-shadow:0 3px 12px rgba(200,164,92,.22)}
  button:hover{filter:brightness(1.12);transform:translateY(-2px);
    box-shadow:0 6px 18px rgba(200,164,92,.32)}
  button:active{transform:translateY(0)}
  button.ghost{background:rgba(255,255,255,.03);border:1px solid var(--line);color:var(--txt);
    font-weight:500;box-shadow:none}
  button.ghost:hover{border-color:var(--gold);color:var(--gold2);background:rgba(200,164,92,.08);
    box-shadow:none}
  button.link{background:transparent;border:1px dashed var(--blue);color:var(--blue);
    font-weight:500;box-shadow:none}
  button.link:hover{background:rgba(74,144,217,.12);box-shadow:none}
  button:disabled{opacity:.35;cursor:not-allowed;transform:none;box-shadow:none}

  .pick{display:flex;gap:14px;margin-top:16px}
  .pick .opt{flex:1;border:1px solid var(--line);border-radius:14px;padding:22px 18px;cursor:pointer;
    background:#0f1620;transition:.22s;text-align:center}
  .pick .opt:hover{border-color:var(--gold);transform:translateY(-3px);
    box-shadow:0 8px 20px rgba(0,0,0,.3)}
  .pick .opt.sel{border-color:var(--gold);background:rgba(200,164,92,.1);
    box-shadow:0 0 22px rgba(200,164,92,.18)}
  .pick .opt .t{font-size:18px;font-weight:700;color:var(--gold2);margin-bottom:8px;letter-spacing:1px}
  .pick .opt .d{font-size:12.2px;color:var(--dim);line-height:1.75}

  .msg{margin-top:12px;font-size:12.5px;padding:10px 14px;border-radius:9px;display:none;line-height:1.6}
  .msg.show{display:block}
  .msg.ok{background:rgba(63,185,80,.12);color:var(--ok);border:1px solid rgba(63,185,80,.32)}
  .msg.err{background:rgba(248,81,73,.12);color:var(--err);border:1px solid rgba(248,81,73,.32)}
  .msg.warn{background:rgba(210,153,34,.12);color:var(--warn);border:1px solid rgba(210,153,34,.32)}

  .hint{margin-top:14px;padding:13px 15px;border-radius:11px;font-size:12.8px;line-height:1.75;
    background:linear-gradient(90deg,rgba(200,164,92,.14),rgba(200,164,92,.03));
    border:1px solid rgba(200,164,92,.36);color:var(--gold2)}
  .hint b{color:#fff8e6}

  .ipbox{margin-top:16px;background:linear-gradient(120deg,#0a1418,#0c1219);
    border:1px solid rgba(62,207,154,.35);border-radius:13px;
    padding:18px 20px;display:flex;align-items:center;justify-content:space-between;gap:14px;
    box-shadow:inset 0 0 22px rgba(62,207,154,.05)}
  .ipbox .lb{font-size:12.2px;color:var(--dim);margin-bottom:4px}
  .ipbox .val{font-size:30px;font-weight:700;color:#eaf6ff;letter-spacing:3px;
    font-family:Consolas,monospace;text-shadow:0 0 20px rgba(90,169,230,.45)}

  ul.tips{margin:12px 0 0 0;padding-left:16px;font-size:12.8px;color:var(--dim);line-height:2;
    border-left:2px solid rgba(200,164,92,.45)}
  ul.tips li{list-style:none;position:relative;padding-left:15px}
  ul.tips li::before{content:"";position:absolute;left:0;top:11px;width:6px;height:6px;
    border-radius:50%;background:var(--gold)}
  ul.tips b{color:var(--txt)}

  .nav{display:flex;justify-content:space-between;align-items:center;margin-top:10px}
  .foot{margin-top:16px;font-size:11.8px;color:#667488;text-align:center;line-height:1.9}
  .foot .it{display:block}
  .legal{margin-top:11px;padding-top:11px;border-top:1px solid rgba(39,52,68,.75);
    font-size:10.6px;color:#556274;line-height:1.8;text-align:center;letter-spacing:.2px}
  .legal p{margin:0 0 5px}
  .legal p:last-child{margin-bottom:0}
  .hide{display:none}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:13px;margin-top:14px}
  .field{background:#0c1219;border:1px solid var(--line);border-radius:12px;padding:13px 15px}
  .field .k{font-size:11.5px;color:var(--dim);margin-bottom:6px;letter-spacing:.5px}
  .field .v{font-size:14.5px;color:var(--txt);word-break:break-all;line-height:1.5}
  .field .v.mono{font-family:Consolas,monospace;color:var(--gold2);font-size:15.5px}
  @keyframes pulse{0%{box-shadow:0 0 0 0 rgba(212,168,87,.45)}
    70%{box-shadow:0 0 0 10px rgba(212,168,87,0)}
    100%{box-shadow:0 0 0 0 rgba(212,168,87,0)}}
  @keyframes fadein{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
</style>
</head>
<body>

<div class="brand">
  <img id="logo" src="data:image/png;base64,__ICON__" alt="logo">
  <div class="bt">
    <h1>地精虫洞发生器</h1>
    <div class="en">GOBLIN WORMHOLE GENERATOR</div>
  </div>
</div>
<div class="sub">Azeroth Survivors · 中国大陆低延迟联机 · v<span id="ver">1.1.0</span></div>

<div class="steps" id="bar">
  <div class="s on" data-i="1"><div class="dot">1</div><span>游戏本体</span></div><div class="ln"></div>
  <div class="s" data-i="2"><div class="dot">2</div><span>虚拟网络</span></div><div class="ln"></div>
  <div class="s" data-i="3"><div class="dot">3</div><span>选择身份</span></div><div class="ln"></div>
  <div class="s" data-i="4"><div class="dot">4</div><span>网络参数</span></div><div class="ln"></div>
  <div class="s" data-i="5"><div class="dot">5</div><span>启动</span></div>
</div>

<!-- Step 1 -->
<div class="card" id="s1">
  <h2>第 1 步 · 选择游戏可执行文件</h2>
  <p>定位 <b>AzerothSurvivors.exe</b>，泰坦装置会在它所在目录启动游戏（否则游戏找不到 Data 会闪退）。</p>
  <div class="row">
    <input type="text" id="game" placeholder="点击右侧按钮选择 AzerothSurvivors.exe" oninput="st.game=this.value.trim();check()">
    <button class="ghost" onclick="pick()">浏览…</button>
    <button class="ghost" onclick="autoDetect()">自动查找</button>
  </div>
  <div class="msg" id="m1"></div>
</div>

<!-- Step 2 -->
<div class="card hide" id="s2">
  <h2>第 2 步 · 确认 Radmin LAN 虚拟网络</h2>
  <p>联机靠 Radmin LAN 把大家拉进同一个虚拟局域网，<b>游戏流量不经过境外服务器</b>，延迟直接降到直连水平。</p>
  <ul class="tips">
    <li>主机和客户机<b>都要安装</b>并加入<b>同一个</b>网络</li>
    <li>加入方式：Radmin LAN → 网络 → 加入网络 → 填网络名 + 密码</li>
    <li>装好后状态应为「在线」，并能拿到 <b>26.x.x.x</b> 开头的地址</li>
  </ul>
  <div class="row">
    <button class="link" onclick="openWeb('home')">访问官网</button>
    <button onclick="openWeb('dl')">下载 Radmin LAN（联想源）</button>
    <button class="ghost" onclick="recheckNet()">检测本机网络</button>
  </div>
  <div class="msg" id="m2"></div>
</div>

<!-- Step 3 -->
<div class="card hide" id="s3">
  <h2>第 3 步 · 你是主机还是客户机？</h2>
  <p>一局只能有一个<b>主机</b>（开房并跑世界逻辑），其他人选<b>客户机</b>加入。建议让网络最好的人当主机。</p>
  <div class="pick">
    <div class="opt" id="opt_host" onclick="pickMode('host')">
      <div class="t">主机</div><div class="d">开一个房间<br>把你的 IP 发给朋友<br>最多 5 人</div>
    </div>
    <div class="opt" id="opt_guest" onclick="pickMode('guest')">
      <div class="t">客户机</div><div class="d">加入朋友的房间<br>需要填主机 IP<br>先让主机开好房</div>
    </div>
  </div>
</div>

<!-- Step 4 -->
<div class="card hide" id="s4">
  <h2 id="t4">第 4 步 · 网络参数</h2>
  <p id="d4"></p>
  <div id="hostArea" class="hide">
    <div class="ipbox">
      <div><div class="lb">客户机需要填写的 IP ↓</div><div class="val" id="myip">检测中…</div></div>
      <button class="ghost" onclick="copyIp()">复制 IP</button>
    </div>
    <div class="row">
      <span style="font-size:11.5px;color:var(--dim)">若自动检测不准，可手动改：</span>
      <input type="text" id="myipIn" class="small" oninput="syncIp()">
    </div>
  </div>
  <div id="guestArea" class="hide">
    <div class="row">
      <input type="text" id="hostIp" placeholder="填写主机给你的 Radmin IP（26.x.x.x）" oninput="st.host_ip=this.value.trim();refreshNav()">
      <button class="ghost" onclick="testConn()">测试连通性</button>
    </div>
    <div class="msg" id="m4t"></div>
  </div>
  <div class="row">
    <span style="font-size:11.5px;color:var(--dim);width:52px">端口</span>
    <input type="text" id="port" class="small" value="7777" oninput="st.port=this.value.trim();refreshNav()">
    <span style="font-size:11.5px;color:var(--dim);width:52px">密码</span>
    <input type="text" id="pwd" class="small" value="mima" oninput="st.password=this.value.trim()">
  </div>
  <div class="msg" id="m4"></div>
</div>

<!-- Step 5 -->
<div class="card hide" id="s5">
  <h2>第 5 步 · 确认并启动</h2>
  <div class="grid2">
    <div class="field"><div class="k">游戏</div><div class="v" id="f_game">-</div></div>
    <div class="field"><div class="k">身份</div><div class="v" id="f_mode">-</div></div>
    <div class="field"><div class="k">地址</div><div class="v mono" id="f_addr">-</div></div>
    <div class="field"><div class="k">端口 / 密码</div><div class="v mono" id="f_pp">-</div></div>
  </div>
  <div class="hint">点击「启动游戏」后，等游戏窗口出现即可 —— <b>本程序随后可以关闭</b>，不会影响联机。</div>
  <div class="msg" id="m5"></div>
</div>

<div class="nav">
  <button class="ghost" id="btnBack" onclick="back()" disabled>上一步</button>
  <button id="btnNext" onclick="next()" disabled>下一步</button>
</div>

<div class="foot">
  <div class="it">本工具仅在你的电脑与好友之间建立直连，不修改游戏文件</div>
  <div class="legal">
    <p>本程序仅作为个人娱乐、技术交流及学习研究之用，不具备任何商业运营属性。严禁任何个人或组织将本程序用于任何形式的商业用途，包括但不限于：</p>
    <p>擅自对本程序进行复制、修改、反编译或衍生开发后用于盈利；<br>
       将本程序或其部分内容用于商业广告、付费服务、捆绑销售等经营活动；<br>
       利用本程序获取非法利益或损害第三方合法权益。</p>
    <p>本程序的所有知识产权归开发者所有。对于违反本公告规定的行为，保留追究相关责任人法律责任的权利。</p>
  </div>
</div>

<script>
var st = {step:1, mode:null, game:"", host_ip:"", myip:"", port:"7777", password:"mima"};

function $(i){return document.getElementById(i)}
function show(n){
  for(var k=1;k<=5;k++) $("s"+k).classList.toggle("hide", k!==n);
  var bar = $("bar").children;
  for(var i=0;i<bar.length;i++){
    var el = bar[i];
    if(!el.classList || !el.classList.contains("s")) continue;
    var idx = parseInt(el.getAttribute("data-i"));
    el.classList.toggle("on", idx===n);
    el.classList.toggle("done", idx<n);
  }
  $("btnBack").disabled = (n===1);
  $("btnNext").textContent = (n===5) ? "启动游戏" : "下一步";
  $("btnNext").disabled = !canGo(n);
}
function msg(id, txt, cls){
  var e = $(id); e.textContent = txt; e.className = "msg show " + cls;
  if(cls!=="warn") setTimeout(function(){ e.classList.remove("show") }, 6000);
}

function canGo(n){
  if(n===1) return !!st.game;
  if(n===3) return !!st.mode;
  if(n===4){
    if(!st.port) return false;
    if(st.mode==="host") return !!st.myip;
    return !!st.host_ip;
  }
  return true;
}

async function pick(){
  var p = await pywebview.api.pick_game();
  if(p){ st.game = p; $("game").value = p; check(); }
}
async function autoDetect(){
  var p = await pywebview.api.default_exe();
  if(p){ st.game = p; $("game").value = p; check(); }
  else msg("m1","没有在常见位置找到游戏，请手动浏览","warn");
}
function refreshNav(){ $("btnNext").disabled = !canGo(st.step); }
async function check(){
  if(!st.game){ refreshNav(); return; }
  var r = await pywebview.api.check_path(st.game);
  msg("m1", r.msg, r.ok ? (r.warn?"warn":"ok") : "err");
  refreshNav();
}
async function openWeb(k){
  await pywebview.api.open_url(k==="home" ? "home" : "dl");
}
async function recheckNet(){
  var r = await pywebview.api.detect_ips();
  if(r.radmin && r.radmin.length){
    msg("m2","已检测到 Radmin 网络地址：" + r.radmin.join("、") + "，网络正常","ok");
  }else{
    msg("m2","没有检测到 26.x.x.x 地址，请确认已安装并加入 Radmin 网络","warn");
  }
}
function pickMode(m){
  st.mode = m;
  $("opt_host").classList.toggle("sel", m==="host");
  $("opt_guest").classList.toggle("sel", m==="guest");
  refreshNav();
}
async function refreshMyIp(){
  var r = await pywebview.api.detect_ips();
  st.myip = (r.radmin && r.radmin[0]) ? r.radmin[0] : ((r.all && r.all[0]) || "");
  $("myip").textContent = st.myip || "未检测到";
  $("myipIn").value = st.myip;
  refreshNav();
}
function syncIp(){ st.myip = $("myipIn").value.trim(); $("myip").textContent = st.myip || "—"; refreshNav(); }
async function copyIp(){
  if(!st.myip) return;
  try{ await navigator.clipboard.writeText(st.myip); msg("m4","IP 已复制：" + st.myip,"ok"); }
  catch(e){ msg("m4","请手动复制：" + st.myip,"warn"); }
}
async function testConn(){
  var ip = $("hostIp").value.trim();
  if(!ip){ msg("m4t","先填主机 IP","warn"); return; }
  st.host_ip = ip;
  var r = await pywebview.api.test_peer(ip, st.port);
  msg("m4t", r.msg, r.ok?"ok":"err");
  refreshNav();
}
function fill5(){
  $("f_game").textContent = st.game || "-";
  $("f_mode").textContent = (st.mode==="host") ? "主机（开房）" : "客户机（加入）";
  $("f_addr").textContent = (st.mode==="host") ? ("本机 " + st.myip + "（发给朋友）") : ("主机 " + st.host_ip);
  $("f_pp").textContent = st.port + " / " + (st.password||"(空)");
}
async function doLaunch(){
  var r = await pywebview.api.launch({
    game: st.game, mode: st.mode, host_ip: st.host_ip,
    port: parseInt(st.port)||7777, password: st.password
  });
  msg("m5", r.msg, r.ok?"ok":"err");
}
function next(){
  if(st.step===4){ st.port = $("port").value.trim() || "7777"; st.password = $("pwd").value.trim(); }
  if(st.step===5){ doLaunch(); return; }
  st.step++;
  if(st.step===2) recheckNet();
  if(st.step===4){
    st.mode==="host" ? $("hostArea").classList.remove("hide") : $("guestArea").classList.remove("hide");
    $("t4").textContent = st.mode==="host" ? "第 4 步 · 你的房间信息" : "第 4 步 · 填入主机 IP";
    $("d4").innerHTML = st.mode==="host"
      ? "这是你在本虚拟网络里的地址，<b>发给朋友</b>填进他们的「主机 IP」。端口和密码大家保持一致。"
      : "向主机索要 IP，填进去后先<b>测试连通性</b>。注意：<b>主机必须先开好房</b>，你才能连上。";
    if(st.mode==="host") refreshMyIp(); else { st.host_ip = $("hostIp").value.trim(); }
  }
  if(st.step===5) fill5();
  show(st.step);
}
function back(){ if(st.step>1){ st.step--; show(st.step); } }

window.addEventListener("pywebviewready", async function(){
  var info = await pywebview.api.app_info();
  $("ver").textContent = info.version;
  var cfg = await pywebview.api.load_config();
  if(cfg.game){ st.game = cfg.game; $("game").value = cfg.game; check(); }
  if(cfg.port) { st.port = cfg.port; $("port").value = cfg.port; }
  if(cfg.password){ st.password = cfg.password; $("pwd").value = cfg.password; }
  if(cfg.host_ip){ st.host_ip = cfg.host_ip; $("hostIp").value = cfg.host_ip; }
  show(1);
});
setInterval(function(){
  pywebview.api.save_config({game:st.game, mode:st.mode, host_ip:st.host_ip,
    port:st.port, password:st.password});
}, 3000);
</script>
</body>
</html>
"""


def main():
    api = Api()
    html = HTML.replace("__ICON__", load_icon_b64())
    window = webview.create_window(
        title=APP_NAME + " · Azeroth Survivors 联机",
        html=html,
        js_api=api,
        width=900,
        height=770,
        min_size=(820, 700),
        background_color="#0b0f16",
        text_select=False,
    )
    api.bind(window)

    # 前端用占位符 home / dl，后端换成真实地址
    _orig_open = api.open_url

    def open_url(key):
        return _orig_open(RADMIN_HOME if key == "home" else RADMIN_DL)

    api.open_url = open_url
    webview.start(debug=False)


if __name__ == "__main__":
    main()
