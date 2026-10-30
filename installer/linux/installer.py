#!/usr/bin/env python3
"""StationDesk native Ubuntu installer. Backend status lines never become commands."""
import os
from pathlib import Path
import queue
import signal
import subprocess
import threading
import tkinter as tk
from tkinter import ttk
from urllib.parse import urlsplit
import webbrowser


def allowed_url(value, login=False):
    try:
        parsed = urlsplit(value)
        return (parsed.scheme == 'https' and bool(parsed.hostname)
                and parsed.username is None and parsed.password is None
                and (not login or parsed.hostname == 'login.tailscale.com'))
    except ValueError:
        return False


class Installer:
    def __init__(self, root, resources):
        self.root, self.resources = root, Path(resources)
        self.language = tk.StringVar(value='English')
        self.paseo = tk.BooleanVar(value=True)
        self.step, self.busy, self.failed = 0, False, False
        self.status, self.logs, self.events = {}, [], queue.Queue()
        self.process, self.command, self.closing = None, None, False
        self.log_open = False
        self.failed_command = None
        self.primary_button = None
        root.bind('<Return>', self.on_return)
        root.title('StationDesk Setup')
        root.geometry('860x620')
        root.minsize(720, 560)
        root.configure(bg='#F5F5F7')
        root.protocol('WM_DELETE_WINDOW', self.close)
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('.', font=('Sans', 11), background='#F5F5F7')
        style.configure('TButton', padding=(14, 10))
        style.configure('Accent.TButton', background='#1F2937', foreground='#F5F0E8')
        style.map('Accent.TButton', background=[('active', '#374151'), ('disabled', '#CFCFD3')])
        style.configure('TCheckbutton', background='white')
        top = tk.Frame(root, bg='#F5F5F7', padx=28, pady=20)
        top.pack(fill='x')
        self.logo_images = []
        try:
            logo = tk.PhotoImage(file=str(self.resources / 'stationdesk.png'))
            self.logo_images = [logo.subsample(32, 32), logo.subsample(21, 21)]
            tk.Label(top, image=self.logo_images[0], bg='#F5F5F7').pack(side='left', padx=(0, 10))
            root.iconphoto(True, self.logo_images[1])
        except tk.TclError:
            pass
        tk.Label(top, text='StationDesk', font=('Sans', 20, 'bold'), bg='#F5F5F7').pack(side='left')
        chooser = ttk.Combobox(top, textvariable=self.language, values=('English', '한국어'), state='readonly', width=10)
        chooser.pack(side='right')
        tk.Label(top, text='Language / 언어', font=('Sans', 10), bg='#F5F5F7', fg='#4A4A50').pack(side='right', padx=(0, 8))
        self.language_combo = chooser
        chooser.bind('<<ComboboxSelected>>', lambda _: self.render())
        self.shell = tk.Frame(root, bg='white', padx=28, pady=24)
        self.shell.pack(fill='both', expand=True, padx=28, pady=(0, 28))
        self.render()
        root.after(80, self.poll)

    def t(self, en, ko):
        return ko if self.language.get() == '한국어' else en

    def label(self, parent, text, **kw):
        kw.setdefault('fg', '#242426')
        widget = tk.Label(parent, text=text, bg='white', justify='left', anchor='w', wraplength=690, **kw)
        widget.pack(fill='x', pady=(0, 4 if self.step == 2 else 14))
        widget.bind('<Configure>', lambda event: widget.configure(wraplength=event.width - 2))
        return widget

    def render(self):
        for widget in self.shell.winfo_children():
            widget.destroy()
        self.primary_button = None
        steps = [self.t('Welcome', '시작'), self.t('Prepare', '준비'), self.t('Apps', '앱'), self.t('Sign in', '로그인'), self.t('Install', '설치'), self.t('Ready', '완료')]
        self.label(self.shell, '  →  '.join(('● ' if i == self.step else '') + s for i, s in enumerate(steps)), font=('Sans', 10), fg='#66666E')
        # Reserve navigation before the expanding body so it cannot be pushed out.
        footer = tk.Frame(self.shell, bg='white')
        footer.pack(side='bottom', fill='x')
        content = tk.Frame(self.shell, bg='white')
        content.pack(fill='both', expand=True)
        titles = [self.t('Your own place to work.', '나만의 작업 공간을 시작하세요.'), self.t('Prepare this Ubuntu machine.', '이 Ubuntu 머신을 준비하세요.'), self.t('The essentials, included.', '기본 앱이 함께 설치됩니다.'), self.t('Connect with Tailscale.', 'Tailscale을 연결하세요.'), self.t('Installation paused.' if self.failed else 'Making room for your work.', '설치가 중단됐습니다.' if self.failed else '작업 공간을 준비하고 있습니다.'), self.t('Your workspace is ready.', '작업 공간이 준비됐습니다.')]
        self.label(content, titles[self.step], font=('Sans', 23, 'bold'))
        descriptions = [self.t('Install the essentials, connect your account, and open StationDesk in your browser.', '기본 앱을 설치하고 계정을 연결한 뒤 브라우저에서 StationDesk를 여세요.'), self.t('StationDesk will install on this machine. The operating system will ask you to approve administrator access in its own dialog.', '이 컴퓨터에 StationDesk를 설치합니다. 관리자 권한은 운영체제의 별도 창에서 승인합니다.'), self.t('Add more apps later in StationDesk.', 'StationDesk에서 나중에 앱을 추가할 수 있습니다.'), self.t('Sign in on Tailscale’s official page, then return here. StationDesk does not ask for your password or API key.', 'Tailscale 공식 페이지에서 로그인한 뒤 돌아오세요. 비밀번호나 API 키는 이 창에서 요구하지 않습니다.'), self.t('Preparing Ubuntu → Installing apps → Checking your address', 'Ubuntu 준비 → 앱 설치 → 접속 주소 확인'), self.t('Open your apps and messages in the browser.', '브라우저에서 앱과 메시지를 확인하세요.')]
        self.label(content, descriptions[self.step], font=('Sans', 11), fg='#66666E')
        if self.step == 1:
            self.label(content, self.t('Ready to prepare.' if self.status.get('prepared') != 'true' else 'System components are ready.', '준비를 시작할 수 있습니다.' if self.status.get('prepared') != 'true' else '시스템 구성 요소가 준비됐습니다.'))
        elif self.step == 2:
            apps = [('StationDesk', 'stationdesk.png', self.t('Home and apps', '홈과 앱')), ('Terminal', 'devterm.png', self.t('Work in a terminal', '터미널 작업')), ('File Viewer', 'fileview.png', self.t('Browse your files', '파일 탐색')), ('Publish', 'publish.png', self.t('Share your work', '작업물 공유')), ('Dev Monitor', 'dev-monitor.png', self.t('Messages and activity', '메시지와 활동'))]
            self.app_icon_images = []
            for name, icon_name, detail in apps:
                row = tk.Frame(content, bg='white')
                row.pack(fill='x')
                icon = None
                icon_path = self.resources / ('icons' if icon_name != 'stationdesk.png' else '') / icon_name
                try:
                    source = tk.PhotoImage(file=str(icon_path))
                    size = max(source.width(), source.height())
                    divisor = max(1, round(size / 24))
                    icon = source.subsample(divisor, divisor)
                    self.app_icon_images.append((source, icon))
                except tk.TclError:
                    pass
                if icon:
                    tk.Label(row, image=icon, bg='white').pack(side='left', padx=(0, 10))
                text = tk.Frame(row, bg='white')
                text.pack(side='left', fill='x', expand=True)
                tk.Label(text, text=name, font=('Sans', 11, 'bold'), fg='#242426', bg='white', anchor='w').pack(side='left')
                tk.Label(text, text=' · ' + detail, font=('Sans', 10), fg='#66666E', bg='white', anchor='w').pack(side='left')
            self.label(content, self.t('Claude Code, Codex, OpenCode, and Antigravity are installed when missing. Sign in to the agents you use after setup.', 'Claude Code·Codex·OpenCode·Antigravity가 없으면 함께 설치합니다. 사용할 에이전트는 설치 후 로그인하세요.'), font=('Sans', 10), fg='#66666E')
            ttk.Separator(content, orient='horizontal').pack(fill='x', pady=(8, 7))
            optional = tk.Frame(content, bg='white')
            optional.pack(fill='x')
            try:
                source = tk.PhotoImage(file=str(self.resources / 'icons' / 'paseo.png'))
                icon = source.subsample(max(1, round(max(source.width(), source.height()) / 24)), max(1, round(max(source.width(), source.height()) / 24)))
                self.app_icon_images.append((source, icon))
                tk.Label(optional, image=icon, bg='white').pack(side='left', padx=(0, 10))
            except tk.TclError:
                pass
            tk.Checkbutton(optional, text=self.t('Paseo · Agent workspace (optional)', 'Paseo · 에이전트 작업 공간 (선택)'), variable=self.paseo, bg='white', activebackground='white', fg='#242426', activeforeground='#242426', highlightthickness=0).pack(side='left')
        elif self.step == 3:
            self.label(content, self.status.get('signed_in') or self.t('Waiting for sign-in.', '로그인을 기다리는 중입니다.'))
            buttons = tk.Frame(content, bg='white')
            buttons.pack(anchor='w')
            if not self.status.get('signed_in'):
                self.action(buttons, self.t('Open Tailscale sign-in ↗', 'Tailscale 로그인 열기 ↗'), self.sign_in).pack(side='left', padx=(0, 10))
            self.action(buttons, self.t('Check sign-in', '로그인 확인'), lambda: self.run('check')).pack(side='left')
        elif self.step == 4:
            self.label(content, self.t('Installation failed. Review the error below, then retry.' if self.failed else 'Installing selected apps…', '설치에 실패했습니다. 아래 오류를 확인한 뒤 다시 시도하세요.' if self.failed else '선택한 앱 설치 중…'))
        elif self.step == 5:
            address = ttk.Entry(content)
            address.insert(0, self.status.get('ready_url', ''))
            address.configure(state='readonly')
            address.pack(fill='x', pady=(0, 14))
        if self.busy:
            progress = ttk.Progressbar(content, mode='indeterminate')
            progress.pack(fill='x', pady=12)
            progress.start(15)
        if self.logs or self.busy:
            ttk.Button(content, text=self.t('Hide log' if self.log_open else 'Show log', '로그 숨기기' if self.log_open else '로그 보기'), command=self.toggle_log).pack(anchor='w', pady=(6, 6))
            if self.log_open or self.failed:
                text = tk.Text(content, height=7, font=('Monospace', 9), bg='#F5F5F7', relief='flat', wrap='word')
                text.pack(fill='both', expand=True)
                text.insert('end', '\n'.join(self.logs[-200:]))
                text.see('end')
                text.configure(state='disabled')
        ttk.Separator(footer, orient='horizontal').pack(fill='x', pady=(15, 0))
        nav = tk.Frame(footer, bg='white')
        nav.pack(fill='x', pady=(15, 0))
        if self.step not in (0, 5):
            self.action(nav, self.t('Back', '뒤로'), self.back, disabled=self.busy).pack(side='left')
        label = self.t('Continue', '계속')
        disabled = self.busy
        if self.step == 0:
            label = self.t('Get started', '시작하기')
        elif self.step == 1 and self.failed and self.failed_command in ('check', 'prepare'):
            label = self.t('Retry', '다시 시도')
        elif self.step == 1 and self.status.get('prepared') != 'true':
            label = self.t('Prepare', '준비하기')
        elif self.step == 3:
            label = self.t('Install StationDesk', 'StationDesk 설치')
            disabled = disabled or not self.status.get('signed_in')
        elif self.step == 4:
            label = self.t('Retry' if self.failed else 'Installing…', '다시 시도' if self.failed else '설치 중…')
            disabled = disabled or not self.failed
        elif self.step == 5:
            label = self.t('Open StationDesk ↗', 'StationDesk 열기 ↗')
        self.primary_button = self.action(nav, label, self.next, disabled=disabled, primary=True)
        self.primary_button.pack(side='right')
        self.primary_button.bind('<Return>', lambda _event: self.primary_button.invoke())

    def on_return(self, event):
        # Let focused controls keep their own keyboard behavior.
        if event.widget == self.root and self.primary_button and not self.primary_button.instate(['disabled']):
            self.primary_button.invoke()
            return 'break'

    def action(self, parent, label, command, disabled=False, primary=False):
        return ttk.Button(parent, text=label, command=command, state='disabled' if disabled or self.busy else 'normal', style='Accent.TButton' if primary else 'TButton')

    def toggle_log(self):
        self.log_open = not self.log_open
        self.render()

    def back(self):
        if not self.busy:
            self.step -= 1
            self.failed = False
            self.failed_command = None
            self.render()

    def next(self):
        if self.busy:
            return
        if self.step == 1 and self.failed and self.failed_command in ('check', 'prepare'):
            self.run(self.failed_command)
        elif self.step == 1 and self.status.get('prepared') != 'true':
            self.run('prepare')
        elif self.step in (3, 4):
            self.step = 4
            self.run('install')
        elif self.step == 5:
            self.open_url(self.status.get('ready_url', ''))
        else:
            self.step += 1
            self.render()
            if self.step in (1, 3):
                self.run('check')

    def open_url(self, url, login=False):
        if allowed_url(url, login):
            webbrowser.open(url)

    def sign_in(self):
        self.run('login')

    def run(self, command):
        if self.busy:
            return
        self.busy, self.failed, self.command = True, False, command
        self.failed_command = None
        self.logs = []
        if command in ('check', 'login'):
            for key in ('signed_in', 'needs_login', 'login_url'):
                self.status.pop(key, None)
        if command == 'install':
            self.status.pop('ready_url', None)
        self.render()
        argv = ['bash', str(self.resources / 'setup.sh'), command]
        if command == 'install' and not self.paseo.get():
            argv.append('--no-paseo')
        def worker():
            try:
                process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace', bufsize=1, start_new_session=True)
                self.events.put(('process', process))
                with process.stdout:
                    for line in process.stdout:
                        self.events.put(('line', line.rstrip('\n')))
                self.events.put(('done', process.wait()))
            except (OSError, UnicodeError) as error:
                self.events.put(('line', str(error)))
                self.events.put(('done', 1))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        redraw = False
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'process':
                    self.process = value
                    if self.closing:
                        self.stop_process()
                elif kind == 'line':
                    parts = value.split('\t', 2)
                    if len(parts) == 3 and parts[0] == 'SD_STATUS':
                        self.status[parts[1]] = parts[2]
                    else:
                        self.logs.append(value)
                    redraw = True
                elif kind == 'done':
                    self.busy, self.process = False, None
                    self.failed = value != 0
                    if self.failed:
                        self.failed_command = self.command
                    if self.command == 'install' and value == 0:
                        if allowed_url(self.status.get('ready_url', '')):
                            self.step = 5
                        else:
                            self.failed = True
                            self.failed_command = self.command
                            self.logs.append('Installation did not return a verified HTTPS address.')
                    if self.command == 'login' and value == 0:
                        self.open_url(self.status.get('login_url', ''), login=True)
                    if self.failed:
                        self.log_open = True
                    redraw = True
        except queue.Empty:
            pass
        if redraw and not self.closing:
            self.render()
        if self.closing:
            if not self.busy:
                self.root.destroy()
                return
        self.root.after(80, self.poll)

    def stop_process(self):
        if self.process and self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            self.root.after(1500, self.kill_process)

    def kill_process(self):
        if self.process and self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def close(self):
        self.closing = True
        if self.busy:
            self.stop_process()
            self.root.withdraw()
        else:
            self.root.destroy()


if __name__ == '__main__':
    root = tk.Tk()
    Installer(root, Path(__file__).resolve().parent)
    root.mainloop()
