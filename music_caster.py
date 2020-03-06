import base64
from datetime import datetime, timedelta
import encodings.idna  # DO NOT REMOVE
import io
from glob import glob
import json
import logging
from math import floor
from pathlib import Path
import platform
from shutil import copyfile
from random import shuffle
from subprocess import Popen
import sys
import threading
import traceback
import uuid
import webbrowser
import zipfile
# 3rd party imports
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template, request, redirect
import requests
import mutagen
from mutagen.id3 import ID3, ID3NoHeaderError
from mutagen.mp3 import MP3
from mutagen.flac import FLAC
from mutagen.easyid3 import EasyID3
import PySimpleGUIWx as SgWx
import wx
import win32com.client
from helpers import *
import helpers


VERSION = '4.27.1'
# TODO: Refactoring. Move all constants and functions to before the try-except
# TODO: move static functions to helpers.py


def fix_path(path):
    if platform.system() == 'Windows': return path.replace('/', '\\')
    else: return path.replace('\\', '/')


def save_json():
    global settings, settings_file
    with open(settings_file, 'w') as outfile:
        json.dump(settings, outfile, indent=4)


def change_settings(settings_key, value):
    global settings, active_windows, tray
    if settings[settings_key] == value: return value
    settings[settings_key] = value
    save_json()
    if settings_key == 'repeat':
        with suppress(IndexError):
            if value: tray.TaskBarIcon.menu[1][11][1] = 'Repeat ✓'
            else: tray.TaskBarIcon.menu[1][11][1] = 'Repeat'
        if active_windows['main']:
            main_window['Repeat'].is_repeating = repeat_setting
            main_window['Repeat'].Update(image_data=REPEAT_SONG_IMG if value else REPEAT_ALL_IMG)
    return value


def update_volume(new_vol):
    """new_vol: float[0, 100]"""
    new_vol = new_vol / 100
    if cast is None:  local_music_player.music.set_volume(new_vol)
    else: cast.set_volume(new_vol)


def get_file_info(file, on_error='FILENAME') -> (str, str):
    try:        
        title = EasyID3(file).get('title', ['Unknown'])[0]
        artist = ', '.join(EasyID3(file).get('artist', ['Unknown']))
        if 'Aevion' in file: print(file)
        return artist, title
    except Exception as e:
        handle_exception(e)
        if on_error == 'FILENAME':
            return os.path.splitext(file)[0]
        return 'Unknown', 'Unknown'


def compile_all_songs(update_global=True, ignore_file='') -> dict:
    global all_songs
    if not update_global:
        temp_songs = all_songs.copy()
        if ignore_file:
            file_info = get_file_info(ignore_file)
            temp_songs.pop(' - '.join(file_info) if type(file_info) == tuple else file_info, None)
        return temp_songs
    all_songs.clear()
    for directory in music_directories:
        for file in glob(f'{directory}/**/*.*', recursive=True):
            if file != ignore_file and valid_music_file(file):
                file_info = get_file_info(file)
                if type(file_info) == tuple: file_info = ' - '.join(file_info)
                all_songs[file_info] = file
    return all_songs


def handle_exception(exception, restart_program=False):
    if settings.get('DEBUG', False) and not getattr(sys, 'frozen', False): raise exception
    current_time = str(datetime.now())
    trace_back_msg = traceback.format_exc()
    mac = ':'.join(['{:02x}'.format((uuid.getnode() >> ele) & 0xff) for ele in range(0, 8 * 6, 8)][::-1])
    with open(f'{starting_dir}/error.log', 'a+') as f:
        f.write(f'{current_time}\nVERSION:{VERSION}\n{trace_back_msg}\n')
    with suppress(requests.ConnectionError):
        requests.post('https://enmuvo35nwiw.x.pipedream.net',
                      json={'TIME': current_time, 'VERSION': VERSION, 'OS': platform.platform(),
                            'TRACEBACK': trace_back_msg, 'MAC': mac})
    if restart_program:
        tray.ShowMessage('Music Caster', 'An error has occurred. Restarting now.')
        time.sleep(5)
        stop()
        os.chdir(starting_dir)
        if getattr(sys, 'frozen', False): os.startfile('Music Caster.exe')
        elif os.path.exists('music_caster.pyw'): Popen(['pythonw', 'music_caster.pyw'])
        else: Popen(['python', 'music_caster.py'])


def download(url, outfile):
    r = requests.get(url, stream=True)
    if outfile.endswith('.zip'):
        outfile = outfile.replace('.zip', '')
        z = zipfile.ZipFile(io.BytesIO(r.content))
        z.extractall(outfile)
    else:
        with open(outfile, 'wb') as f:
            f.write(r.content)


def download_and_extract(link, infile, outfile=None):
    r = requests.get(link, stream=True)
    z = zipfile.ZipFile(io.BytesIO(r.content))
    z.extractall(f'{starting_dir}/Update')  # extract to a folder called Update
    if outfile is None: outfile = infile
    with suppress(FileNotFoundError): os.remove(outfile)
    os.rename(f'{starting_dir}/Update/{infile}', outfile)


PORT, WAIT_TIMEOUT = 2001, 10
update_devices, cast = False, None
chromecasts, device_names = [], []
starting_dir = os.path.dirname(os.path.realpath(__file__))
home_music_dir = str(Path.home()) + '/Music'
settings = {  # default settings
        'previous_device': None, 'accent_color': '#00bfff', 'text_color': '#aaaaaa', 'button_text_color': '#000000',
        'background_color': '#121212', 'volume': 100, 'scrubbing_delta': 5, 'volume_delta': 5, 'auto_update': False,
        'run_on_startup': True, 'notifications': True, 'shuffle_playlists': True, 'repeat': False,
        'timer_shut_off_computer': False, 'timer_hibernate_computer': False, 'timer_sleep_computer': False,
        'EXPERIMENTAL': False, 'music_directories': [home_music_dir], 'playlists': {}}
settings_file = f'{starting_dir}/settings.json'
playlists, tray_playlists = {}, ['Create/Edit a Playlist']
music_directories, notifications_enabled = [], True
all_songs = {}
pl_name, pl_files = '', []
keyboard_command = main_window = settings_window = timer_window = pl_editor_window = pl_selector_window = None
mouse_hover = main_last_event = settings_last_event = pl_editor_last_event = None
open_pl_selector = update_progress_text = False
new_playing_text, timer = 'Nothing Playing', 0
active_windows = {'main': False, 'settings': False, 'timer': False, 'playlist_selector': False,
                  'playlist_editor': False}
if os.path.exists('MC_installer.exe'): os.remove('MC_Installer.exe')


def load_settings():
    """load (and fix if needed) the settings file"""
    # TODO: update any GUI values
    global settings, playlists, notifications_enabled, music_directories, tray_playlists, DEFAULT_DIR, settings_last_loaded
    if os.path.exists(settings_file):
        with open(settings_file) as json_file:
            try: loaded_settings = json.load(json_file)
            except json.decoder.JSONDecodeError as e: loaded_settings = {}
            save_settings = False
            for setting_name, setting_value in tuple(loaded_settings.items()):
                loaded_settings[setting_name.replace(' ', '_')] = loaded_settings.pop(setting_name)
            for setting_name, setting_value in settings.items():
                if setting_name not in loaded_settings:
                    loaded_settings[setting_name] = setting_value
                    save_settings = True
            settings = loaded_settings
            playlists = settings['playlists']
            tray_playlists.clear()
            tray_playlists.append('Create/Edit a Playlist')
            tray_playlists += [f'PL: {pl}' for pl in playlists.keys()]
            notifications_enabled = settings['notifications']
            temp = music_directories.copy()
            music_directories = settings['music_directories']
            if not music_directories: music_directories = change_settings('music_directories', [home_music_dir])
            if temp != music_directories: compile_all_songs()
            del temp
            DEFAULT_DIR = music_directories[0]
        if save_settings: save_json()
    else: save_json()
    settings_last_loaded = time.time()


load_settings()
if not settings.get('DEBUG', False) and is_already_running():
    while True:
        with suppress(requests.exceptions.InvalidSchema):
            if PORT == 2100 or requests.get(f'http://127.0.0.1:{PORT}/instance/').text == 'True': break
        PORT += 1
    sys.exit()


if settings['auto_update']:
    with suppress(requests.ConnectionError):
        github_url = 'https://github.com/elibroftw/music-caster/releases'
        html_doc = requests.get(github_url).text
        soup = BeautifulSoup(html_doc, features='html.parser')
        release_entries = soup.find_all('div', class_='release-entry')
        for entry in release_entries:
            latest_version = entry.find('a', class_='muted-link css-truncate')['title'][1:]
            release_type = entry.find('span').text.strip()
            if release_type == 'Latest release': break
        major, minor, patch = (int(x) for x in VERSION.split('.'))
        lt_major, lt_minor, lt_patch = (int(x) for x in latest_version.split('.'))
        if (lt_major > major or lt_major == major and lt_minor > minor
                or lt_major == major and lt_minor == minor and lt_patch > patch):
            details = entry.find('details',
                                 class_='details-reset Details-element border-top pt-3 mt-4 mb-2 mb-md-4')
            download_links = [link['href'] for link in details.find_all('a') if link.get('href')]
            setup_download_link = f'https://github.com{download_links[0]}'
            bundle_download_link = f'https://github.com{download_links[1]}'
            source_download_link = f'https://github.com{download_links[-2]}'
            os.chdir(starting_dir)
            tray = SgWx.SystemTray(menu=['File', []], data_base64=UNFILLED_ICON, tooltip='Music Caster')
            tray.ShowMessage('Music Caster', f'Downloading Update v{latest_version}')
            tray.Update(tooltip='Downloading Update...')
            try:
                if settings.get('DEBUG'):
                    print(setup_download_link)
                    print(bundle_download_link)
                    print(source_download_link)
                elif getattr(sys, 'frozen', False):
                    if not os.path.exists('Updater.exe'):
                        download(bundle_download_link, 'Portable.zip')
                        os.rename('Portable/Updater.exe', 'Updater.exe')
                        # download_and_extract(bundle_download_link, 'Updater.exe')
                    os.startfile('Updater.exe')
                elif os.path.exists('updater.py') or os.path.exists('music_caster.py'):
                    download_and_extract(source_download_link, f'music-caster-{latest_version}/updater.py',
                                         'updater.py')
                    Popen(['pythonw', 'updater.py'])
                elif os.path.exists('updater.pyw') or os.path.exists('music_caster.pyw'):
                    download_and_extract(source_download_link, f'music-caster-{latest_version}/updater.py',
                                         'updater.pyw')
                    Popen(['pythonw', 'updater.pyw'])
            except Exception as e:
                tray.ShowMessage('Music Caster', 'Auto update failed')
                tray.Update(tooltip='Auto update failed')
                change_settings('auto_update', False)
                handle_exception(e)
                time.sleep(5)
                if getattr(sys, 'frozen', False): os.startfile('Music Caster.exe')
            tray.Hide()
            sys.exit()


if not settings.get('DEBUG', False):
    with suppress(requests.exceptions.ConnectionError):
        mac = ':'.join(['{:02x}'.format((uuid.getnode() >> ele) & 0xff) for ele in range(0, 8 * 6, 8)][::-1])
        requests.post('https://en3ay96poz86qa9.m.pipedream.net', json={'MAC': mac, 'VERSION': VERSION, 'TIME': datetime.now().strftime('%m/%d/%Y %H:%M:%S')})
    

app = Flask(__name__, static_folder='/', static_url_path='/')
try:
    # from PIL import Image
    import pychromecast.controllers.media
    from pychromecast.error import UnsupportedNamespace
    import pychromecast
    from pygame import mixer as local_music_player
    from pynput.keyboard import Listener
    import winshell

    local_music_player.init(44100, -16, 2, 2048)
    helpers.ACCENT_COLOR, helpers.fg, helpers.bg = settings['accent_color'], settings['text_color'], settings['background_color']
    helpers.BUTTON_COLOR = (settings['button_text_color'], helpers.ACCENT_COLOR)
    Sg.SetOptions(button_color=BUTTON_COLOR, scrollbar_color=bg, background_color=bg, element_background_color=bg, text_element_background_color=bg)


    @app.route('/', methods=['GET', 'POST'])
    def home():  # web GUI
        global music_queue, playing_status
        if request.args:
            if 'play' in request.args:
                if playing_status == 'PAUSED': resume()
                elif music_queue: play_file(music_queue[0])
                else: play_all()
            elif 'pause' in request.args and playing_status == 'PLAYING': pause()
            elif 'next' in request.args: next_song()
            elif 'prev' in request.args: previous()
            elif 'repeat' in request.args: change_settings('repeat', not settings['repeat'])
            elif 'shuffle' in request.args: change_settings('shuffle', not settings['shuffle'])
            return redirect('/')
        if music_queue:
            file_path = music_queue[0]
            metadata = music_meta_data[file_path]
        else: metadata = {'artist': 'N/A', 'title': 'Nothing Playing', 'album': 'N/A'}
        art = metadata.get('art', Path(f'{images_dir}/default.png').as_uri()[11:])
        repeat_option = 'red' if settings['repeat'] else ''
        shuffle_option = 'red' if settings['shuffle_playlists'] else ''
        return render_template('home.html', main_button='pause' if playing_status == 'PLAYING' else 'play', repeat=repeat_option,
                               shuffle=shuffle_option, art=art, metadata=metadata, starting_dir=Path(starting_dir).as_uri()[11:])

    @app.route('/metadata/')
    def metadata():
        if music_queue:
            file_path = music_queue[0]
            metadata = music_meta_data[file_path]
        else: metadata = {'artist': 'N/A', 'title': 'Nothing Playing', 'album': 'N/A'}
        return jsonify(metadata)

    
    @app.route('/running/')
    def running():
        return 'True'


    @app.route('/instance/')
    def instance():
        global keyboard_command
        for k, v in active_windows.items():  # Opens up GUI
            if v:
                if k == 'main': main_window.bring_to_front()
                elif k == 'settings': settings_window.bring_to_front()
                elif k == 'timer': timer_window.bring_to_front()
                elif k == 'playlist_selector': pl_selector_window.bring_to_front()
                else: pl_editor_window.bring_to_front()  # playlist_editor
                return 'True'
        keyboard_command = '__ACTIVATED__'
        return 'True'


    @app.errorhandler(404)
    def page_not_found(_):
        return '404 error', 404

    def chromecast_callback(chromecast):
        global update_devices, cast, chromecasts
        previous_device = settings['previous_device']
        if str(chromecast.uuid) == previous_device and cast != chromecast:
            cast = chromecast
            cast.wait(timeout=WAIT_TIMEOUT)
        if chromecast.uuid not in [cc.uuid for cc in chromecasts]:
            chromecasts.append(chromecast)
            chromecasts.sort(key=lambda cc: (cc.name, cc.uuid))
            device_names.clear()
            for i, cc in enumerate(['Local device'] + chromecasts):
                name = cc if i == 0 else cc.name
                if (previous_device is None and i == 0) or (type(cc) != str and str(cc.uuid) == previous_device):
                    device_names.append(f'✓ {name}')
                else: device_names.append(f'{i + 1}. {name}')
            update_devices = True


    def startup_setting(path):
        run_on_startup = settings['run_on_startup']
        shortcut_exists = os.path.exists(path)
        if run_on_startup and not shortcut_exists and not settings.get('DEBUG', False):
            shell = win32com.client.Dispatch('WScript.Shell')
            shortcut = shell.CreateShortCut(path)
            if getattr(sys, 'frozen', False):  # Running in a bundle
                target = f'{starting_dir}\\Music Caster.exe'
            else:  # set shortcut to python script; __file__
                bat_file = f'{starting_dir}\\music_caster.bat'
                if os.path.exists(bat_file):
                    with open('music_caster.bat', 'w') as f:
                        f.write(f'pythonw {os.path.basename(__file__)}')
                target = bat_file
                shortcut.IconLocation = f'{starting_dir}\\icon.ico'
            shortcut.Targetpath = target
            shortcut.WorkingDirectory = starting_dir
            shortcut.WindowStyle = 1  # 7 - Minimized, 3 - Maximized, 1 - Normal
            shortcut.save()
        elif not run_on_startup and shortcut_exists: os.remove(path)


    shortcut_path = f'{winshell.startup()}\\Music Caster.lnk'
    # Access startup folder by entering "Startup" in Explorer address bar
    # Only one of the below can be True
    temp = (settings['timer_shut_off_computer'], settings['timer_hibernate_computer'], settings['timer_sleep_computer'])
    if temp.count(True) > 1:
        if settings['timer_shut_off_computer']: change_settings('timer_hibernate_computer', False)
        change_settings('timer_sleep_computer', False)

    images_dir = starting_dir + '/images'
    cc_music_dir = starting_dir + '/music files'
    if not os.path.exists(cc_music_dir): os.mkdir(cc_music_dir)
    if not os.path.exists(images_dir): os.mkdir(images_dir)
    if not os.path.exists(f'{images_dir}/default.png'):  # in case the user decided to delete the default image
        if os.path.exists('resources/default.png'):  # running from source code
            copyfile('resources/default.png', 'images/default.png')
        else:  # download the default image
            with suppress(requests.ConnectionError):
                default_img = 'https://raw.githubusercontent.com/elibroftw/music-caster/master/resources/default.png'
                response = requests.get(default_img, stream=True)
                with open(f'{images_dir}/default.png', 'wb') as handle:
                    for data in response.iter_content(): handle.write(data)
    for file in glob(f'{cc_music_dir}/*.*') + glob(f'{images_dir}/*.*'):
        if not file.endswith('default.png'): os.remove(file)
    with open(f'{images_dir}/default.png', 'rb') as f:
        DEFAULT_IMG_DATA = base64.b64encode(f.read())  # TODO RESIZE
    os.chdir(os.getcwd()[:3])  # set drive as the working dir
    logging.getLogger('werkzeug').disabled = True
    os.environ['WERKZEUG_RUN_MAIN'] = 'true'
    while True:
        try:
            if not is_port_in_use(PORT):
                threading.Thread(target=app.run, daemon=True, kwargs={'host': '0.0.0.0', 'port': PORT, 'debug': False}).start()
                break
            else: PORT += 1
        except OSError: PORT += 1
    startup_setting(shortcut_path)
    stop_discovery = pychromecast.get_chromecasts(blocking=False, callback=chromecast_callback)
    discovery_started = time.time()
    
    # TODO: add play folder
    repeat_setting = 'Repeat ✓' if settings['repeat'] else 'Repeat'
    menu_def_1 = ['', ['Settings', 'Refresh Devices', 'Select Device', device_names, 'Playlists', tray_playlists,
                       'Timer', ['Set Timer', 'Cancel Timer'], 'Play', ['Play File', 'Play All'], 'Exit']]

    menu_def_2 = ['', ['Settings', 'Refresh Devices', 'Select Device', device_names, 'Playlists', tray_playlists,
                       'Timer', ['Set Timer', 'Cancel Timer'], 'Play', ['Play File', 'Play File Next', 'Play All'],
                       'Controls',
                       ['Locate File', repeat_setting, 'Stop', 'Previous Song', 'Next Song', 'Pause'],
                       'Exit']]

    menu_def_3 = ['', ['Settings', 'Refresh Devices', 'Select Device', device_names, 'Playlists', tray_playlists,
                       'Timer', ['Set Timer', 'Cancel Timer'], 'Play', ['Play File', 'Play File Next', 'Play All'],
                       'Controls',
                       ['Locate File', repeat_setting, 'Stop', 'Previous Song', 'Next Song', 'Resume'],
                       'Exit']]
    tooltip = 'Music Caster [DEBUG]' if settings.get('DEBUG', False) else 'Music Caster'
    tray = SgWx.SystemTray(menu=menu_def_1, data_base64=UNFILLED_ICON, tooltip=tooltip)
    if notifications_enabled: tray.ShowMessage('Music Caster', 'Music Caster is running in the tray', time=500)
    if not music_directories:
        music_directories = change_settings('music_directories', [home_music_dir])
        compile_all_songs()
    DEFAULT_DIR = music_directories[0]
    music_queue, done_queue, next_queue = [], [], []
    music_meta_data = {}  # file: {artist: str, title: str}
    song_end = song_length = song_start = 0  # seconds but using time()
    progress_bar_last_update = song_position = 0  # also seconds but relative to length of song
    mc, playing_status, time_left = None, 'NOT PLAYING', 0
    settings_last_loaded = cast_last_checked = time.time()


    def play_file(file_path, position=0, autoplay=True, switching_device=False):
        global mc, song_start, song_end, playing_status, song_length, song_position, volume, images_dir,\
               cast_last_checked, music_queue
        while not os.path.exists(file_path):
            music_queue.remove(file_path)
            if music_queue: file_path = music_queue[0]
            else: return
            position = 0
        song_position = position
        audio_info = mutagen.File(file_path).info
        song_length = audio_info.length
        volume = settings['volume'] / 100
        try:
            title = EasyID3(file_path).get('title', ['Unknown'])[0]
            artist = EasyID3(file_path).get('artist', ['Unknown'])
            artist = ', '.join(artist)
            album = EasyID3(file_path).get('album', 'Unknown')[0]
        except Exception as e:
            handle_exception(e)
            title = artist = album = 'Unknown'
        # thumb, album_cover_data = get_album_cover(file_path)
        # music_meta_data[file_path] = {'artist': artist, 'title': title, 'album': album, 'length': song_length,
        #                               'album_cover_data': album_cover_data}
        pict = None
        if file_path.lower().endswith('mp3'): tags = MP3(file_path)
        elif file_path.lower().endswith('flac'): tags = FLAC(file_path)
        else:
            try: tags = ID3(file_path)
            except ID3NoHeaderError: tags = mutagen.File(file_path)
        for tag in tags.keys():
            if 'APIC' in tag:
                pict = tags[tag].data
                break
        if pict:
            music_meta_data[file_path] = {'artist': artist, 'title': title, 'album': album, 'length': song_length, 'art': f'data:image/png;base64,{base64.b64encode(pict).decode("utf-8")}'}
        else: music_meta_data[file_path] = {'artist': artist, 'title': title, 'album': album, 'length': song_length}
        if cast is None:  # play locally
            mc = None
            sample_rate = audio_info.sample_rate
            if local_music_player.get_init() is None or local_music_player.get_init()[0] != sample_rate:
                local_music_player.quit()
                local_music_player.init(sample_rate, -16, 2, 2048)
            local_music_player.music.load(file_path)
            local_music_player.music.set_volume(volume)
            local_music_player.music.play(start=song_position)
            if not autoplay: local_music_player.music.pause()
            song_start = time.time() - song_position
            song_end = song_start + song_length
        else:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(('8.8.8.8', 80))
                ipv4_address = s.getsockname()[0]
                s.close()
                drive = file_path[:3]
                file_path_obj = Path(file_path)
                if drive != os.getcwd().replace('\\', '/'):
                    new_file_path = f'{cc_music_dir}/{file_path_obj.name}'
                    copyfile(file_path, new_file_path)
                else: new_file_path = file_path
                uri_safe = Path(new_file_path).as_uri()[11:]
                url = f'http://{ipv4_address}:{PORT}/{uri_safe}'
                if pict:
                    thumb = images_dir + f'/{file_path_obj.stem}.png'
                    with open(thumb, 'wb') as f: f.write(pict)
                else: thumb = f'{images_dir}/default.png'
                thumb = f'http://{ipv4_address}:{PORT}/{Path(thumb).as_uri()[11:]}'
                cast.wait(timeout=WAIT_TIMEOUT)
                cast.set_volume(volume)
                mc = cast.media_controller
                if mc.status.player_is_playing or mc.status.player_is_paused:
                    mc.stop()
                    mc.block_until_active(5)
                music_metadata = {'metadataType': 3, 'albumName': album, 'title': title, 'artist': artist}
                mc.play_media(url, f'audio/{file_path.split(".")[-1]}', current_time=song_position,
                              metadata=music_metadata, thumb=thumb, autoplay=autoplay)
                mc.block_until_active()
                while mc.status.player_state not in {'PLAYING', 'PAUSED'}: time.sleep(0.1)
                song_start = time.time() - song_position
                song_end = song_start + song_length
            except (pychromecast.error.NotConnected, OSError):
                tray.ShowMessage('Music Caster', 'Could not connect to Chromecast device')
                with suppress(pychromecast.error.UnsupportedNamespace): stop()
                return
        playing_text = f"{artist.split(', ')[0]} - {title}"
        if notifications_enabled and not settings['repeat'] and not switching_device:
            tray.ShowMessage('Music Caster','Playing: ' + playing_text, time=500)
        if autoplay:
            playing_status = 'PLAYING'
            tray.Update(menu=menu_def_2, data_base64=FILLED_ICON, tooltip=playing_text)
        cast_last_checked = time.time()


    def play_all(starting_file=None):
        global playing_status
        music_queue.clear()
        done_queue.clear()
        if starting_file: music_queue.extend(compile_all_songs(False, starting_file).values())
        else: music_queue.extend(all_songs.values())
        if music_queue:
            shuffle(music_queue)
            if starting_file: music_queue.insert(0, starting_file)
            play_file(music_queue[0])
            tray.Update(menu=menu_def_2, data_base64=FILLED_ICON)
        elif next_queue:
            playing_status = 'PLAYING'
            next_song()


    def play_next():
        global music_directories, DEFAULT_DIR, playing_status
        if music_directories: DEFAULT_DIR = music_directories[0]
        else: DEFAULT_DIR = home_music_dir
        fd = wx.FileDialog(None, 'Select Music File', defaultDir=DEFAULT_DIR, wildcard='Audio File (*.mp3)|*mp3',
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        if fd.ShowModal() != wx.ID_CANCEL:
            path_to_file = fd.GetPath()
            next_queue.append(path_to_file)
            if playing_status == 'NOT PLAYING':
                if cast is not None and cast.app_id != 'CC1AD845': cast.wait(timeout=WAIT_TIMEOUT)
                playing_status = 'PLAYING'
                next_song()


    # def get_album_cover(file_path, only_b64=True):
    #     file_path_obj = Path(file_path)
    #     thumb = images_dir + f'/{file_path_obj.stem}.png'
    #     tags = ID3(file_path)
    #     pict = None
    #     for tag in tags.keys():
    #         if 'APIC' in tag:
    #             pict = tags[tag]
    #             break
    #     if pict is not None:
    #         raw = pict = pict.data
    #         with open(thumb, 'wb') as f: f.write(pict)
    #     else:
    #         thumb = f'{images_dir}/default.png'
    #         with open(thumb, 'rb') as f: raw = f.read()
    #     data = io.BytesIO(raw)
    #     im = Image.open(data)
    #     raw = io.BytesIO()
    #     new_h = 150
    #     h_percent = (new_h / float(im.size[1]))
    #     new_w = int((float(im.size[0]) * float(h_percent)))
    #     im = im.resize((new_w, new_h), Image.ANTIALIAS)
    #     im.save(raw, optimize=True, format='PNG')
    #     return thumb, raw.getvalue()


    def update_song_position():
        global tray, song_position, cast
        if cast is not None:
            mc = cast.media_controller
            if mc is not None:
                try:
                    mc.update_status()
                    song_position = mc.status.adjusted_current_time
                except UnsupportedNamespace: song_position = time.time() - song_start
        elif playing_status == 'PLAYING': song_position = time.time() - song_start
        return song_position


    def pause():
        global tray, playing_status, song_position
        tray.Update(menu=menu_def_3, data_base64=UNFILLED_ICON)
        try:
            if mc is not None:
                mc.update_status()
                mc.pause()
                while not mc.status.player_is_paused: time.sleep(0.1)
                song_position = mc.status.adjusted_current_time
            else:
                song_position = time.time() - song_start
                local_music_player.music.pause()
            playing_status = 'PAUSED'
        except UnsupportedNamespace:
            playing_status = 'NOT PLAYING'


    def resume():
        global tray, playing_status, song_end, song_position, song_start
        tray.Update(menu=menu_def_2, data_base64=FILLED_ICON)
        try:
            if mc is not None:
                mc.update_status()
                mc.play()
                mc.block_until_active()
                while not mc.status.player_state == 'PLAYING': time.sleep(0.1)
                song_position = mc.status.adjusted_current_time
            else: local_music_player.music.unpause()
            song_start = time.time() - song_position
            song_end = song_start + song_length
            playing_status = 'PLAYING'
        except UnsupportedNamespace:
            play_file(music_queue[0], position=song_position)


    def stop():
        global playing_status, cast, song_position, time_left
        playing_status = 'NOT PLAYING'
        if mc is not None and cast is not None and cast.app_id == 'CC1AD845':
            mc.stop()
            while mc.is_playing or mc.is_paused: time.sleep(0.1)
        elif local_music_player.music.get_busy():
            local_music_player.music.stop()
            # local_music_player.music.unload()  # only in 2.0
        tray.Update(menu=menu_def_1, data_base64=UNFILLED_ICON, tooltip='Music Caster')


    def next_song(from_timeout=False):
        global playing_status
        if cast is not None and cast.app_id != 'CC1AD845':
            playing_status = 'NOT PLAYING'
        elif playing_status != 'NOT PLAYING' and next_queue or music_queue:
            if not settings['repeat'] or not from_timeout or not music_queue:
                change_settings('repeat', False)
                if music_queue: done_queue.append(music_queue.pop(0))
                if next_queue: music_queue.insert(0, next_queue.pop(0))
            if music_queue: play_file(music_queue[0])
            elif done_queue:
                music_queue.extend(done_queue)
                done_queue.clear()
                play_file(music_queue[0])
            else: stop()


    def previous():
        global playing_status
        if cast is not None and cast.app_id != 'CC1AD845': playing_status = 'NOT PLAYING'
        elif playing_status != 'NOT PLAYING':
            if done_queue:
                change_settings('repeat', False)
                song = done_queue.pop()
                music_queue.insert(0, song)
                play_file(song)
            elif music_queue: play_file(music_queue[0])


    def on_press(key):
        global keyboard_command
        if str(key) == '<179>':
            if playing_status == 'PLAYING': keyboard_command = 'Pause'
            elif playing_status == 'PAUSED': keyboard_command = 'Resume'
        elif str(key) == '<176>': keyboard_command = 'Next Song'
        elif str(key) == '<177>': keyboard_command = 'Previous Song'
        elif str(key) == '<178>': keyboard_command = 'Stop'

    
    listener_thread = Listener(on_press=on_press)
    listener_thread.start()
    while True:
        menu_item = tray.Read(timeout=10)
        if time.time() - settings_last_loaded > 10:
            load_settings()
            if playing_status == 'PLAYING': tray.Update(menu=menu_def_2)
            elif playing_status == 'PAUSED': tray.Update(menu=menu_def_3)
            else: tray.Update(menu=menu_def_1)
        if discovery_started and time.time() - discovery_started > 10:
            discovery_started = 0
            stop_discovery()
            if not device_names:
                device_names.append(f'✓ Local device')
        if menu_item == 'Refresh Devices':
            load_settings()
            update_devices = True
            stop_discovery()
            chromecasts.clear()
            stop_discovery = pychromecast.get_chromecasts(blocking=False, callback=chromecast_callback)
            discovery_started = time.time()
        if update_devices:
            update_devices = False
            if playing_status == 'PLAYING': tray.Update(menu=menu_def_2)
            elif playing_status == 'PAUSED': tray.Update(menu=menu_def_3)
            else: tray.Update(menu=menu_def_1)
        if '__ACTIVATED__' in {menu_item, keyboard_command}:
            if not active_windows['main']:
                active_windows['main'] = True
                volume = settings['volume']
                repeat_setting = settings['repeat']
                if playing_status in {'PAUSED', 'PLAYING'}:
                    current_song = music_queue[0]
                    metadata = music_meta_data[current_song]
                    artist, title = metadata['artist'].split(', ')[0], metadata['title']
                    new_playing_text = f'{artist} - {title}'
                    album_cover_data = metadata.get('album_cover_data', None)
                    main_gui_layout = create_main_gui(music_queue, done_queue, next_queue, playing_status,
                                                      volume, repeat_setting, all_songs, new_playing_text,
                                                      album_cover_data=album_cover_data)
                else:
                    main_gui_layout = create_main_gui(music_queue, done_queue, next_queue, playing_status,
                                                      volume, repeat_setting, all_songs)
                main_window = Sg.Window('Music Caster', main_gui_layout, background_color=bg, icon=WINDOW_ICON,
                                        return_keyboard_events=True, use_default_focus=False)
                dq_len = len(done_queue)
                main_window.Finalize()
                main_window['music_queue'].Update(set_to_index=dq_len, scroll_to_index=dq_len)
                main_window['Pause/Resume'].playing_status = playing_status
                main_window['Repeat'].is_repeating = repeat_setting
                main_window['volume'].bind('<Enter>', '_mouse_enter')
                main_window['volume'].bind('<Leave>', '_mouse_leave')
                main_window['progressbar'].bind('<Enter>', '_mouse_enter')
                main_window['progressbar'].bind('<Leave>', '_mouse_leave')
                main_window['tab2'].bind('<Enter>', '_mouse_enter')
                main_window['tab2'].bind('<Leave>', '_mouse_leave')
            main_window.TKroot.focus_force()
            main_window.normal()
        elif menu_item.split('.')[0].isdigit():  # if user selected a different device
            i = device_names.index(menu_item)
            if i == 0: new_cast = None
            else:
                try:
                    new_cast = chromecasts[i - 1]
                except IndexError: new_cast = None
            device_names.clear()
            for index, cc in enumerate(['Local device'] + chromecasts):
                name = cc if index == 0 else cc.name
                if index == i: device_names.append(f'✓ {name}')
                else: device_names.append(f'{index + 1}. {name}')
            update_devices = True
            if cast != new_cast:
                cast = new_cast
                volume = settings['volume'] / 100
                if cast is None:
                    change_settings('previous_device', None)
                    local_music_player.music.set_volume(volume)
                else:
                    change_settings('previous_device', str(cast.uuid))
                    cast.wait(timeout=WAIT_TIMEOUT)  # TODO: fix Chromecast is connection error
                    cast.set_volume(volume)
                current_pos = 0
                if local_music_player.music.get_busy():
                    if playing_status == 'PLAYING': current_pos = time.time() - song_start
                    else: current_pos = song_position
                    local_music_player.music.stop()
                elif mc is not None:
                    with suppress(UnsupportedNamespace):
                        mc.update_status()  # Switch device without playback loss
                        current_pos = mc.status.adjusted_current_time
                        if mc.is_playing or mc.is_paused: mc.stop()
                mc = None if cast is None else cast.media_controller
                if playing_status in {'PAUSED', 'PLAYING'}:
                    do_autoplay = False if playing_status == 'PAUSED' else True
                    play_file(music_queue[0], position=current_pos, autoplay=do_autoplay, switching_device=True)
        elif menu_item == 'Settings':
            if not active_windows['settings']:
                active_windows['settings'] = True
                # RELIEFS: RELIEF_RAISED RELIEF_SUNKEN RELIEF_FLAT RELIEF_RIDGE RELIEF_GROOVE RELIEF_SOLID
                settings_layout = create_settings(VERSION, music_directories, settings)
                settings_window = Sg.Window('Music Caster Settings', settings_layout, background_color=bg,
                                            icon=WINDOW_ICON, return_keyboard_events=True, use_default_focus=False)
                # settings_window.Read(timeout=1)
                settings_window.Finalize()
                # settings_window['volume'].bind('<Enter>', '_mouse_enter')
                # settings_window['volume'].bind('<Leave>', '_mouse_leave')
            settings_window.TKroot.focus_force()
            settings_window.normal()
        elif menu_item == 'Create/Edit a Playlist':
            if active_windows['playlist_editor']:
                pl_editor_window.TKroot.focus_force()
                pl_editor_window.normal()
                continue
            elif not active_windows['playlist_selector']:
                load_settings()
                active_windows['playlist_selector'] = True
                pl_selector_window = Sg.Window('Playlist Selector', playlist_selector(playlists), background_color=bg,
                                               icon=WINDOW_ICON, return_keyboard_events=True)
                pl_selector_window.Read(timeout=1)
            pl_selector_window.TKroot.focus_force()
            pl_selector_window.normal()
        elif menu_item.startswith('PL: '):
            playlist = menu_item[4:]
            music_queue.clear()
            music_queue.extend(playlists[playlist])
            if music_queue:
                done_queue.clear()
                if settings['shuffle_playlists']: shuffle(music_queue)
                play_file(music_queue[0])
                tray.Update(menu=menu_def_2, data_base64=FILLED_ICON)
        elif menu_item == 'Set Timer':
            if not active_windows['timer']:
                active_windows['timer'], timer_layout = True, create_timer(settings)
                timer_window = Sg.Window('Music Caster Set Timer', timer_layout, background_color=bg, icon=WINDOW_ICON,
                                         return_keyboard_events=True, grab_anywhere=True)
                timer_window.Read(timeout=1)
            timer_window.TKroot.focus_force()
            timer_window.normal()
            timer_window.Element('minutes').SetFocus()
        elif menu_item == 'Cancel Timer':
            timer = 0
            if notifications_enabled: tray.ShowMessage('Music Caster', 'Timer stopped')
        elif menu_item == 'Play File':
            if music_directories: DEFAULT_DIR = music_directories[0]
            else: DEFAULT_DIR = home_music_dir
            fd = wx.FileDialog(None, 'Select Music File', defaultDir=DEFAULT_DIR, wildcard='Audio File (*.mp3)|*mp3',
                               style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
            if fd.ShowModal() != wx.ID_CANCEL:
                play_all(fd.GetPath())
        elif menu_item == 'Play All': play_all()
        elif menu_item.startswith('PF: '):  # play folder
            menu_item = menu_item[4:]
        elif menu_item == 'Play File Next': play_next()
        elif 'Stop' in {menu_item, keyboard_command}: stop()
        elif timer and time.time() > timer:
            stop()
            timer = 0
            if settings['timer_shut_off_computer']:
                if platform.system() == 'Windows': os.system('shutdown /p /f')
                else: os.system('sudo shutdown now')
            elif settings['timer_hibernate_computer']:
                if platform.system() == 'Windows': os.system(r'rundll32.exe powrprof.dll,SetSuspendState Hibernate')
                else: pass
            elif settings['timer_sleep_computer']:
                if platform.system() == 'Windows': os.system('rundll32.exe powrprof.dll,SetSuspendState 0,1,0')
                else: pass
        elif ('Next Song' in {menu_item, keyboard_command} and playing_status != 'NOT PLAYING'
              or playing_status == 'PLAYING' and time.time() > song_end):
            next_song(from_timeout=time.time() > song_end)
        elif 'Previous Song' in {menu_item, keyboard_command} and playing_status != 'NOT PLAYING': previous()
        elif menu_item in {'Repeat', 'Repeat ✓'}:
            repeat_setting = change_settings('repeat', not settings['repeat'])
            if notifications_enabled:
                if repeat_setting: tray.ShowMessage('Music Caster', 'Repeating current song')
                else: tray.ShowMessage('Music Caster', 'Not repeating current song')
        elif 'Resume' in {menu_item, keyboard_command}: resume()
        elif 'Pause' in {menu_item, keyboard_command}: pause()
        elif menu_item in 'Locate File':
            if music_queue: Popen(f'explorer /select,"{fix_path(music_queue[0])}"')
        elif menu_item == 'Exit':
            tray.Hide()
            with suppress(UnsupportedNamespace):
                stop()
                # if cast is not None and cast.app_id == 'CC1AD845': cast.quit_app()
                # Commented because I am unsure if it is effective
            break
        
        # MAIN WINDOW
        if active_windows['main']:
            main_event, main_values = main_window.Read(timeout=1)
            if main_event is None:
                active_windows['main'] = False
                main_window.CloseNonBlocking()
                continue
            if main_event in {'q', 'Q'} or main_event == 'Escape:27' and main_last_event != 'Add Folder':
                active_windows['main'] = False
                main_window.CloseNonBlocking()
            main_last_event = main_event
            p_r_button = main_window['Pause/Resume']
            now_playing_text = main_window['now_playing']
            update_text = update_repeat_img = False  # text refers to now playing text

            if main_event.startswith('MouseWheel'):
                main_event = main_event.split(':', 1)[1]
                delta = {'Up': 5, 'Down': -5}.get(main_event, 0)
                if mouse_hover == 'progressbar':
                    if playing_status in {'PLAYING', 'PAUSED'}:
                        main_event = 'progressbar'
                        update_song_position()
                        new_position = min(max(song_position + delta, 0), song_length) / song_length * 100
                        main_window['progressbar'].Update(value=new_position)
                        main_values['progressbar'] = new_position
                elif mouse_hover == '':  # not in another tab
                    main_event = 'volume'
                    new_volume = min(max(0, main_values['volume'] + delta), 100)
                    main_window['volume'].Update(value=new_volume)
                    main_values['volume'] = new_volume
                main_window.Refresh()
            # if main_event != '__TIMEOUT__': print(main_event)
            if main_event == 'progressbar_mouse_enter': mouse_hover = 'progressbar'
            elif main_event == 'progressbar_mouse_leave': mouse_hover = ''
            # elif main_event == 'volume_mouse_enter': mouse_hover = 'volume'
            # elif main_event == 'volume_mouse_leave': mouse_hover = ''
            elif main_event == 'tab2_mouse_enter': mouse_hover = 'tab2'
            elif main_event == 'tab2_mouse_leave': mouse_hover = ''
            elif main_event == 'tab3_mouse_enter': mouse_hover = 'tab3'
            elif main_event == 'tab3_mouse_leave': mouse_hover = ''
            elif main_event in {'Locate File', 'e:69'}:
                if music_queue: Popen(f'explorer /n,/select,"{fix_path(music_queue[0])}"')
            elif main_event == 'Pause/Resume':
                if playing_status == 'PAUSED': resume()
                elif playing_status == 'PLAYING': pause()
                elif playing_status == 'NOT PLAYING' and music_queue: play_file(music_queue[0])
                else: play_all()
            elif main_event == 'Next' and playing_status != 'NOT PLAYING': next_song(); progress_bar_last_update = 0
            elif main_event == 'Prev' and playing_status != 'NOT PLAYING': previous(); progress_bar_last_update = 0
            elif main_event == 'Shuffle':
                # TODO: just shuffle music queue
                pass
            elif main_event == 'Repeat':
                repeat_setting = change_settings('repeat', not settings['repeat'])
                # update_repeat_img = True
                # main_window['Repeat'].is_repeating = repeat_setting
            elif main_event in {'volume', 'a', 'd'} or main_event.isdigit():
                delta = 0
                if main_event.isdigit():
                    update_slider = True
                    new_volume = int(main_event) * 10
                else:
                    update_slider = False
                    if main_event == 'a': delta = -5
                    elif main_event == 'd': delta = 5
                    new_volume = main_values['volume'] + delta
                change_settings('volume', new_volume)
                if update_slider or delta != 0: main_window['volume'].Update(value=new_volume)
                update_volume(new_volume)
            elif main_event in {'Up:38', 'Down:40', 'Prior:33', 'Next:34'}:
                with suppress(AttributeError, IndexError):
                    if main_window.FindElementWithFocus() == main_window['music_queue']:
                        move = {'Up:38': -1, 'Down:40': 1, 'Prior:33': -3, 'Next:34': 3}[main_event]
                        new_i = main_window['music_queue'].GetListValues().index(main_values['music_queue'][0]) + move
                        new_i = min(max(new_i, 0), len(music_queue) - 1)
                        main_window['music_queue'].Update(set_to_index=new_i, scroll_to_index=new_i)
            elif main_event == 'move_up':
                try: index_to_move = main_window['music_queue'].GetListValues().index(main_values['music_queue'][0])
                except IndexError: index_to_move = -1
                if index_to_move > 0:
                    new_i = index_to_move - 1
                    dq_len = len(done_queue)
                    nq_len = len(next_queue)
                    if index_to_move < dq_len:  # move within dq
                        done_queue.insert(new_i, done_queue.pop(index_to_move))
                    elif index_to_move == dq_len:  # move index -1 to 1
                        if next_queue: next_queue.insert(1, done_queue.pop())
                        else: music_queue.insert(1, done_queue.pop())
                    elif index_to_move == dq_len + 1:  # move 1 to -1
                        if next_queue: done_queue.append(next_queue.pop(0))
                        else: done_queue.append(music_queue.pop(1))
                    elif index_to_move < dq_len + nq_len + 1:  # within next_queue
                        nq_i = new_i - dq_len - 1
                        next_queue.insert(nq_i, next_queue.pop(nq_i + 1))
                    elif next_queue and index_to_move == dq_len + nq_len + 1:  # moving into next queue
                        next_queue.insert(nq_len - 1, music_queue.pop(1))
                    else:  # moving within mq
                        mq_i = new_i - dq_len - nq_len
                        music_queue.insert(mq_i, music_queue.pop(mq_i + 1))
                    updated_list = create_songs_list(music_queue, done_queue, next_queue)[0]
                    main_window['music_queue'].Update(values=updated_list,
                                                      set_to_index=new_i, scroll_to_index=new_i)
            elif main_event == 'move_down':
                try: index_to_move = main_window['music_queue'].GetListValues().index(main_values['music_queue'][0])
                except IndexError: index_to_move = -1
                dq_len, nq_len, mq_len = len(done_queue), len(next_queue), len(music_queue)
                if index_to_move == -1: pass
                elif index_to_move < dq_len + nq_len + mq_len - 1:
                    new_i = index_to_move + 1
                    if index_to_move == dq_len - 1:  # move index -1 to 1
                        if next_queue: next_queue.insert(0, done_queue.pop())
                        else: music_queue.insert(1, done_queue.pop())
                    elif index_to_move < dq_len:  # move within dq
                        done_queue.insert(new_i, done_queue.pop(index_to_move))
                    elif index_to_move == dq_len:  # move 1 to -1
                        if next_queue: done_queue.append(next_queue.pop(0))
                        else: done_queue.append(music_queue.pop(1))
                    elif next_queue and index_to_move == dq_len + nq_len:  # moving into music_queue
                        music_queue.insert(2, next_queue.pop())
                    elif index_to_move < dq_len + nq_len + 1:  # within next_queue
                        nq_i = index_to_move - dq_len - 1
                        next_queue.insert(nq_i, next_queue.pop(nq_i - 1))
                    else:  # within music_queue
                        mq_i = new_i - dq_len - nq_len
                        music_queue.insert(mq_i, music_queue.pop(mq_i - 1))
                    updated_list = create_songs_list(music_queue, done_queue, next_queue)[0]
                    main_window['music_queue'].Update(values=updated_list,
                                                      set_to_index=new_i, scroll_to_index=new_i)
            elif main_event == 'remove':
                with suppress(IndexError):
                    index_to_remove = main_window['music_queue'].GetListValues().index(main_values['music_queue'][0])
                    dq_len, nq_len, mq_len = len(done_queue), len(next_queue), len(music_queue)
                    if index_to_remove < dq_len:
                        done_queue.pop(index_to_remove)
                    elif index_to_remove == dq_len:
                        music_queue.pop(0)
                        play_file(music_queue[0])
                    elif index_to_remove <= nq_len + dq_len:
                        next_queue.pop(index_to_remove - dq_len - 1)
                    elif index_to_remove < nq_len + mq_len + dq_len:
                        music_queue.pop(index_to_remove - dq_len - nq_len)
                    updated_list = create_songs_list(music_queue, done_queue, next_queue)[0]
                    new_i = min(len(updated_list), index_to_remove)
                    main_window['music_queue'].Update(values=updated_list,
                                                      set_to_index=new_i, scroll_to_index=new_i)
            elif main_event == 'queue_file':
                if music_directories: DEFAULT_DIR = music_directories[0]
                else: DEFAULT_DIR = home_music_dir
                fd = wx.FileDialog(None, 'Select Music File', defaultDir=DEFAULT_DIR, wildcard='Audio File (*.mp3)|*mp3',
                                style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
                if fd.ShowModal() != wx.ID_CANCEL:
                    playing_file = fd.GetPath()
                    music_queue.append(playing_file)
                pass
            elif main_event == 'play_next': play_next()
            elif main_event == 'locate_file': Popen(f'explorer /select,"{fix_path(music_queue[0])}"')
            elif main_event == 'library': play_all(all_songs[main_values['library']])
            if main_event == 'progressbar':
                if playing_status == 'NOT PLAYING':
                    main_window['progressbar'].Update(disabled=True)
                    # maybe even make it invisible?
                    continue
                new_position = main_values['progressbar'] / 100 * song_length
                song_position = new_position
                if cast is not None:
                    cast.media_controller.seek(new_position)
                else:
                    local_music_player.music.rewind()
                    local_music_player.music.set_pos(new_position)
                    # local_music_player.music.set_pos(new_position - song_position)
                    # song_position = new_position
                time_left = song_length - song_position
                song_end = time.time() + time_left
                song_start = song_end - song_length
                update_progress_text = True
            if playing_status in {'PLAYING', 'PAUSED'} and time.time() - progress_bar_last_update > 1:
                # TODO: progressbar visible if playing?
                if music_queue:
                    metadata = music_meta_data[music_queue[0]]
                    artist, title = metadata['artist'].split(', ')[0], metadata['title']
                    new_playing_text = f'{artist} - {title}'
                    update_text = now_playing_text.DisplayText != new_playing_text
                    progress_bar = main_window['progressbar']
                    update_song_position()
                    progress_bar.Update(song_position / song_length * 100, disabled=False)
                    time_left = song_length - song_position
                    update_progress_text = True
                    progress_bar_last_update = time.time()
                else: playing_status = 'NOT PLAYING'
            if update_progress_text:
                mins_elapsed, mins_left = floor(song_position / 60), floor(time_left / 60)
                secs_elapsed, secs_left = floor(song_position % 60), floor(time_left % 60)
                if secs_left < 10: secs_left = f'0{secs_left}'
                if secs_elapsed < 10: secs_elapsed = f'0{secs_elapsed}'
                main_window['time_elapsed'].Update(value=f'{mins_elapsed}:{secs_elapsed}')
                main_window['time_left'].Update(value=f'{mins_left}:{secs_left}')
                metadata = music_meta_data[music_queue[0]]
                # main_window['album_cover'].Update(data=metadata['album_cover_data'])
                update_progress_text = False
            # if update_repeat_img or settings['repeat'] != main_window['Repeat'].is_repeating:
            #     if repeat_setting: main_window['Repeat'].Update(image_data=REPEAT_SONG_IMG)
            #     else: main_window['Repeat'].Update(image_data=REPEAT_ALL_IMG)
            #     main_window['Repeat'].is_repeating = settings['repeat']
            lb_music_queue: Sg.Listbox = main_window['music_queue']
            dq_len = len(done_queue)
            update_lb_mq = len(lb_music_queue.get_list_values()) != len(music_queue) + len(next_queue) + dq_len
            if playing_status == 'PLAYING' and p_r_button.playing_status != 'PLAYING':
                p_r_button.playing_status = 'PLAYING'
                p_r_button.Update(image_data=PAUSE_BUTTON_IMG)
            elif playing_status == 'PAUSED' and p_r_button.playing_status != 'PAUSED':
                p_r_button.playing_status = 'PAUSED'
                p_r_button.Update(image_data=PLAY_BUTTON_IMG)
            elif playing_status == 'NOT PLAYING' and p_r_button.playing_status != 'NOT PLAYING':
                if p_r_button.playing_status == 'PLAYING': p_r_button.Update(image_data=PLAY_BUTTON_IMG)
                p_r_button.playing_status, new_playing_text, update_text = 'NOT PLAYING', 'Nothing Playing', True
                main_window['time_elapsed'].Update(value='00:00')
                main_window['time_left'].Update(value='00:00')
            if update_text: now_playing_text.Update(value=new_playing_text)
            if update_text or update_lb_mq:
                lb_music_queue_songs = create_songs_list(music_queue, done_queue, next_queue)[0]
                lb_music_queue.Update(values=lb_music_queue_songs, set_to_index=dq_len, scroll_to_index=dq_len)

        # SETTINGS WINDOW
        if active_windows['settings']:
            # TODO: handle delete key
            settings_event, settings_values = settings_window.Read(timeout=1)
            if settings_event is None:
                active_windows['settings'] = False
                settings_window.CloseNonBlocking()
                continue
            settings_value = settings_values.get(settings_event)
            if settings_event in {'q', 'Q'} or settings_event == 'Escape:27' and settings_last_event != 'Add Folder':
                active_windows['settings'] = False
                settings_window.CloseNonBlocking()
            # if settings_event.startswith('MouseWheel'):  #  and mouse_hover == 'volume'
            #     settings_event = settings_event.split(':', 1)[1]
            #     delta = {'Up': 5, 'Down': -5}.get(settings_event, 0)
            #     settings_event = 'volume'
            #     new_volume = min(max(0, settings_values['volume'] + delta), 100)
            #     settings_window['volume'].Update(value=new_volume)
            #     settings_values['volume'] = new_volume
            # if settings_event == 'volume_mouse_enter': mouse_hover = 'volume'
            # elif settings_event == 'volume_mouse_leave': mouse_hover = ''
            if settings_event == 'email':
                webbrowser.open('mailto:elijahllopezz@gmail.com?subject=Regarding%20Music%20Caster')
            elif settings_event in {'auto_update', 'run_on_startup', 'notifications', 'shuffle_playlists'}:
                change_settings(settings_event, settings_value)
                if settings_event == 'run_on_startup': startup_setting(shortcut_path)
                elif settings_event == 'notifications': notifications_enabled = settings_value
            # elif settings_event in {'volume', 'a', 'd'} or settings_event.isdigit():
            #     delta = 0
            #     if settings_event.isdigit():
            #         update_slider = True
            #         new_volume = int(settings_event) * 10
            #     else:
            #         update_slider = False
            #         if settings_event == 'a': delta = -5
            #         elif settings_event == 'd': delta = 5
            #         new_volume = settings_values['volume'] + delta
            #     change_settings('volume', new_volume)
            #     if update_slider or delta != 0: settings_window.Element('volume').Update(value=new_volume)
            #     update_volume(new_volume)
            elif settings_event == 'Remove Folder' and settings_values['music_dirs']:
                selected_item = settings_values['music_dirs'][0]
                if selected_item in music_directories:
                    music_directories.remove(selected_item)
                    compile_all_songs()
                    save_json()
                    settings_window.Element('music_dirs').Update(music_directories)
            elif settings_event == 'Add Folder':
                if settings_value not in music_directories and os.path.exists(settings_value):
                    music_directories.append(settings_value)
                    compile_all_songs()
                    save_json()
                    settings_window.Element('music_dirs').Update(music_directories)
                    # TODO: update menu "Play Folder" list
            elif settings_event == 'Open Settings':
                try: os.startfile(settings_file)
                except OSError:
                    Popen(f'explorer /select,"{fix_path(settings_file)}"')
            settings_last_event = settings_event
        if active_windows['playlist_selector']:
            pl_selector_event, pl_selector_values = pl_selector_window.Read(timeout=1)
            if pl_selector_event in {None, 'Escape:27', 'q', 'Q'}:
                active_windows['playlist_selector'] = False
                pl_selector_window.CloseNonBlocking()
                continue
            if pl_selector_event in {'del_pl', 'Delete:46'}:
                pl_name = pl_selector_values.get('pl_selector', '')
                if pl_name in playlists: del playlists[pl_name]
                # new_values = list(playlists.keys())
                # value = new_values[0] if new_values else ''
                # pl_selector_window.Element('pl_selector').Update(value=value, values=new_values)
                pl_selector_window.CloseNonBlocking()
                pl_selector_window = Sg.Window('Playlist Selector', playlist_selector(playlists), background_color=bg,
                                               icon=WINDOW_ICON, return_keyboard_events=True)
                pl_selector_window.Read(timeout=1)
                pl_selector_window.TKroot.focus_force()
                pl_selector_window.normal()
                save_json()
                tray_playlists.clear()
                tray_playlists.append('Create/Edit a Playlist')
                tray_playlists += [f'PL: {pl}' for pl in playlists.keys()]
                if playing_status == 'PLAYING': tray.Update(menu=menu_def_2)
                elif playing_status == 'PAUSED': tray.Update(menu=menu_def_3)
                else: tray.Update(menu=menu_def_1)
            elif pl_selector_event in {'edit_pl', 'create_pl', 'e', 'n', 'e:69', 'n:78'}:
                if pl_selector_event in {'edit_pl', 'e', 'e:69'}: pl_name = pl_selector_values.get('pl_selector', '')
                else: pl_name = ''
                # https://github.com/PySimpleGUI/PySimpleGUI/issues/845#issuecomment-443862047
                pl_editor_window = Sg.Window('Playlist Editor', playlist_editor(DEFAULT_DIR, playlists, pl_name),
                                             background_color=bg, icon=WINDOW_ICON, return_keyboard_events=True)
                pl_files = playlists.get(pl_name, [])
                pl_selector_window.CloseNonBlocking()
                pl_editor_window.Read(timeout=1)
                pl_editor_window.TKroot.focus_force()
                pl_editor_window.normal()
                if pl_selector_event == 'create_pl': pl_editor_window.Element('playlist_name').SetFocus()
                else:
                    pl_editor_window.Element('songs').SetFocus()
                    pl_editor_window.Element('songs').Update(set_to_index=0)
                active_windows['playlist_editor'], active_windows['playlist_selector'] = True, False
        if active_windows['playlist_editor']:
            pl_editor_event, pl_editor_values = pl_editor_window.Read(timeout=1)
            if pl_editor_event in {None, 'Escape:27', 'q:81', 'Cancel'} and pl_editor_last_event != 'Add songs':
                active_windows['playlist_editor'] = False
                pl_editor_window.CloseNonBlocking()
                open_pl_selector = True
            elif pl_editor_event in {'Save', 's:83'}:
                new_name = pl_editor_values['playlist_name']
                pl_files = pl_files.copy()
                if pl_name != new_name:
                    if pl_name in playlists: del playlists[pl_name]
                    pl_name = new_name
                playlists[pl_name] = pl_files
                save_json()
                active_windows['playlist_editor'] = False
                pl_editor_window.CloseNonBlocking()
                open_pl_selector = True
                tray_playlists.clear()
                tray_playlists.append('Create/Edit a Playlist')
                tray_playlists += [f'PL: {pl}' for pl in playlists.keys()]
                if playing_status == 'PLAYING': tray.Update(menu=menu_def_2)
                elif playing_status == 'PAUSED': tray.Update(menu=menu_def_3)
                else: tray.Update(menu=menu_def_1)
            elif pl_editor_event in {'move_up', 'u:85'}:  # u:85 is Ctrl + U
                if pl_editor_values['songs']:
                    index_to_move = pl_editor_window.Element('songs').GetListValues().index(pl_editor_values['songs'][0])
                    if index_to_move > 0:
                        new_i = index_to_move - 1
                        pl_files.insert(new_i, pl_files.pop(index_to_move))
                        formatted_songs = [f'{i+1}. {os.path.basename(path)}' for i, path in enumerate(pl_files)]
                        pl_editor_window.Element('songs').Update(values=formatted_songs, set_to_index=new_i,
                                                                 scroll_to_index=new_i)
            elif pl_editor_event in {'move_down', 'd:68'}:  # d:68 is Ctrl + D
                if pl_editor_values['songs']:
                    index_to_move = pl_editor_window.Element('songs').GetListValues().index(pl_editor_values['songs'][0])
                    if index_to_move < len(pl_files) - 1:
                        new_i = index_to_move + 1
                        pl_files.insert(new_i, pl_files.pop(index_to_move))
                        formatted_songs = [f'{i+1}. {os.path.basename(path)}' for i, path in enumerate(pl_files)]
                        pl_editor_window.Element('songs').Update(values=formatted_songs, set_to_index=new_i,
                                                                 scroll_to_index=new_i)
            elif pl_editor_event == 'Add songs':
                selected_songs = pl_editor_values['Add songs']
                if selected_songs:
                    new_files = [file for file in selected_songs.split(';') if valid_music_file(file)]
                    pl_files += new_files
                    pl_editor_window.TKroot.focus_force()
                    pl_editor_window.normal()
                    # current_songs = pl_editor_window.Element('songs').GetListValues()
                    formatted_songs = [f'{i+1}. {os.path.basename(path)}' for i, path in enumerate(pl_files)]
                    new_i = len(formatted_songs) - 1  # - len(new_files)
                    pl_editor_window['songs'].Update(formatted_songs, set_to_index=new_i, scroll_to_index=new_i)
            elif pl_editor_event in {'Remove song', 'r:82'}:  # r:82 is Ctrl + R
                if pl_editor_values['songs']:
                    index_to_rm = pl_editor_window['songs'].GetListValues().index(pl_editor_values['songs'][0])
                    with suppress(ValueError): pl_files.pop(index_to_rm)
                    formatted_songs = [f'{i+1}. {os.path.basename(path)}' for i, path in enumerate(pl_files)]
                    new_i = max(index_to_rm - 1, 0)
                    pl_editor_window['songs'].Update(formatted_songs, set_to_index=new_i, scroll_to_index=new_i)
            elif pl_editor_event in {'Up:38', 'Down:40', 'Prior:33', 'Next:34'}:
                move = {'Up:38': -1, 'Down:40': 1, 'Prior:33': -3, 'Next:34': 3}[pl_editor_event]
                new_i = pl_editor_window['songs'].GetListValues().index(pl_editor_values['songs'][0]) + move
                new_i = min(max(new_i, 0), len(pl_files) - 1)
                pl_editor_window['songs'].Update(set_to_index=new_i, scroll_to_index=new_i)
            if open_pl_selector:
                open_pl_selector = False
                active_windows['playlist_selector'] = True
                pl_selector_window = Sg.Window('Playlist Selector', playlist_selector(playlists), background_color=bg,
                                               icon=WINDOW_ICON, return_keyboard_events=True)
                pl_selector_window.Read(timeout=1)
                pl_selector_window.TKroot.focus_force()
                pl_selector_window.normal()
            pl_editor_last_event = pl_editor_event
        if active_windows['timer']:
            timer_event, timer_values = timer_window.Read(timeout=1)
            if timer_event is None:
                active_windows['timer'] = False
                timer_window.CloseNonBlocking()
                continue
            timer_value = timer_values.get(timer_event, None)
            if timer_event in {'Escape:27', 'q', 'Q'}:
                active_windows['timer'] = False
                timer_window.CloseNonBlocking()
            elif timer_event in {'\r', 'special 16777220', 'special 16777221', 'Submit'}:
                try:
                    timer_value = timer_values['minutes']
                    if timer_value.isdigit():
                        minutes = abs(float(timer_values['minutes']))
                    elif timer_value.count(':') == 1:
                        now = datetime.now()
                        # meridiem = time.strftime('%p')
                        if timer_value[-3:].strip().upper() in ('PM', 'AM'): timer_value = timer_value[timer_value:-3]
                        elif timer_value[-2:].upper() in ('PM', 'AM'): timer_value = timer_value[timer_value:-2]
                        to_stop = datetime.strptime(timer_value + time.strftime(',%Y,%m,%d,%p'), '%I:%M,%Y,%m,%d,%p')
                        delta = (to_stop - datetime.now()).total_seconds()
                        # Suppose you want to stop music at at 12:00 AM, but it's 11:00 PM.
                        # 12:00 AM -> would make delta = -39,600 seconds (-11 Hours)
                        # We want this to be 3600 seconds
                        # 43,200 - 39,600 = 3600
                        if delta < 0: delta += 43200
                        minutes = delta // 60
                    else: continue
                    timer = time.time() + 60 * minutes
                    if notifications_enabled:
                        timer_set_to = datetime.now() + timedelta(minutes=minutes)
                        timer_set_to = timer_set_to.strftime('%#I:%M %p')
                        # timer_set_to = timer_set_to.strftime('%-I:%M %p')  # Linux
                        tray.ShowMessage('Music Caster', f'Timer set for {timer_set_to}', time=500)
                    active_windows['timer'] = False
                    timer_window.CloseNonBlocking()
                except ValueError:
                    Sg.PopupOK('Input a number!')
            elif timer_event in {'shut_off', 'hibernate', 'sleep'}:
                change_settings('timer_hibernate_computer', timer_values['hibernate'])
                change_settings('timer_sleep_computer', timer_values['sleep'])
                change_settings('timer_shut_off_computer', timer_values['shut_off'])
        keyboard_command = None
        if mc is not None and time.time() - cast_last_checked > 5:
            with suppress(UnsupportedNamespace):
                if cast is not None:
                    if cast.app_id == 'CC1AD845':
                        mc.update_status()
                        is_playing, is_paused = mc.status.player_is_playing, mc.status.player_is_paused
                        new_song_position = mc.status.adjusted_current_time
                        volume = settings['volume']
                        cast_volume = cast.status.volume_level * 100
                        song_start = time.time() - new_song_position  # if music was scrubbed on the home app
                        song_end = time.time() + song_length - new_song_position
                        song_position = new_song_position
                        if is_paused and playing_status != 'PAUSED': pause()
                        elif is_playing and playing_status != 'PLAYING': resume()
                        elif not (is_playing or is_paused) and playing_status != 'NOT PLAYING': stop()
                        if volume != cast_volume:
                            volume = change_settings('volume', cast_volume)
                            if active_windows['settings']: settings_window['volume'].Update(volume)
                            if active_windows['main']: main_window['volume'].Update(volume)
                    elif playing_status in {'PAUSED', 'PLAYING'}: stop()
            cast_last_checked = time.time()
except Exception as e:
    handle_exception(e, True)
