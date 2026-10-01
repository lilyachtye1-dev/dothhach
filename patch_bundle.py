"""Recognize, validate and safely stage a multi-file offline patch bundle.

Supported JSON names: client_dll.json, offsets.json, ks_profiles.json,
buttons.json and info.json. No archive member is extracted and no JSON is executed.
The standard offsets.json adapter is explicit; buttons.json is retained for reference
but is not mapped to HACHBOB hotkeys (those remain user key bindings in profiles).
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import uuid
import zipfile

import patch_update as updater

ROOT = Path(__file__).resolve().parent
MAX_JSON = 24 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
MAX_MEMBERS = 2000
RECOGNIZED = {'client_dll.json', 'offsets.json', 'ks_profiles.json', 'buttons.json', 'info.json'}
STAGE_POINTER = '.patch_staged'
STAGE_DIR = '.patch_staging'


def _basename(name):
    return str(name).replace('\\', '/').rsplit('/', 1)[-1].casefold()


def _add(found, ignored, name, data):
    base = _basename(name)
    if base not in RECOGNIZED:
        ignored.append(str(name))
        return
    if base in found:
        raise ValueError('В пакете несколько файлов с именем ' + base + '; выбери один актуальный комплект.')
    if not isinstance(data, bytes) or len(data) > MAX_JSON:
        raise ValueError(base + ': файл пустой или превышает 24 MiB.')
    found[base] = data


def collect_files(paths, allow_profiles=True):
    """Read selected JSON/ZIP files by basename without extracting archives.

    The ordinary in-app updater passes allow_profiles=False so users can import
    offsets but cannot replace the developer-maintained ability catalog.
    """
    found, ignored, total = {}, [], 0
    if not paths:
        raise ValueError('Не выбраны файлы JSON или ZIP.')
    if len(paths) > 100:
        raise ValueError('Выбрано слишком много файлов (лимит 100).')
    for path in paths:
        path = Path(path).expanduser()
        if not path.is_file():
            raise ValueError('Файл не найден: ' + str(path))
        if zipfile.is_zipfile(path):
            try:
                with zipfile.ZipFile(path) as archive:
                    members = [m for m in archive.infolist() if not m.is_dir()]
                    if len(members) > MAX_MEMBERS:
                        raise ValueError('В архиве больше 2000 файлов.')
                    for member in members:
                        base = _basename(member.filename)
                        if not allow_profiles and base == 'ks_profiles.json':
                            raise ValueError('Импорт ks_profiles.json через меню отключён: каталог способностей ведёт разработчик.')
                        if base not in RECOGNIZED:
                            ignored.append(member.filename)
                            continue
                        if member.file_size > MAX_JSON or total + member.file_size > MAX_TOTAL:
                            raise ValueError('Размер распознаваемых JSON в архиве превышает лимит.')
                        with archive.open(member, 'r') as stream:
                            data = stream.read(MAX_JSON + 1)
                        total += len(data)
                        if len(data) != member.file_size:
                            raise ValueError('Неполное чтение из архива: ' + member.filename)
                        _add(found, ignored, member.filename, data)
            except zipfile.BadZipFile as exc:
                raise ValueError('Не удалось прочитать ZIP: ' + str(exc))
        elif path.suffix.casefold() == '.json':
            if not allow_profiles and path.name.casefold() == 'ks_profiles.json':
                raise ValueError('Импорт ks_profiles.json через меню отключён: каталог способностей ведёт разработчик.')
            size = path.stat().st_size
            if size > MAX_JSON or total + size > MAX_TOTAL:
                raise ValueError('Общий размер JSON превышает лимит.')
            data = path.read_bytes()
            total += len(data)
            _add(found, ignored, path.name, data)
        else:
            ignored.append(path.name)
    missing = [name for name in ('client_dll.json', 'offsets.json') if name not in found]
    if missing:
        present = ', '.join(sorted(found)) or 'нет распознанных JSON'
        raise ValueError('Для патча нужны client_dll.json и offsets.json. Найдено: ' + present +
                         '; отсутствует: ' + ', '.join(missing))
    return found, ignored


def _offset(group, key, alternate=None):
    if not isinstance(group, dict):
        raise ValueError('В offsets.json отсутствует корректная группа ' + key)
    value = group.get(key)
    if value is None and alternate:
        value = group.get(alternate)
    if value is None:
        raise ValueError('В offsets.json отсутствует поле ' + key)
    return value


def offsets_manifest(offsets_data, schema_data):
    """Map the known standard offsets.json keys to the exact HACHBOB v1 manifest."""
    doc = updater.load(offsets_data)
    if not isinstance(doc, dict):
        raise ValueError('offsets.json должен содержать JSON-объект.')
    client = doc.get('client.dll')
    engine = doc.get('engine2.dll')
    if not isinstance(client, dict) or not isinstance(engine, dict):
        raise ValueError('offsets.json должен содержать группы client.dll и engine2.dll.')
    game_system = _offset(client, 'dwGameEntitySystem', 'dwEntityList')
    entity_list = client.get('dwEntityList')
    if entity_list is not None and updater.number(entity_list, 0x40000000) != updater.number(game_system, 0x40000000):
        raise ValueError('dwEntityList и dwGameEntitySystem не совпадают; нельзя выбрать между ними автоматически.')
    manifest = {
        'format': 'hachbob-offsets-v1',
        'schema_sha256': updater.digest(schema_data),
        'build': updater.number(_offset(engine, 'dwBuildNumber'), 0x7FFFFFFF),
        'client.dll': {
            'DW_GAME_ENTITY_SYSTEM': game_system,
            'DW_VIEW_MATRIX': _offset(client, 'dwViewMatrix'),
            'DW_VIEW_RENDER': _offset(client, 'dwViewRender'),
            'DW_GAME_RULES': _offset(client, 'dwGameRules'),
            'DW_GLOBAL_VARS': _offset(client, 'dwGlobalVars'),
        },
        'engine2.dll': {
            'DW_NETWORK_GAME_CLIENT': _offset(engine, 'dwNetworkGameClient'),
            'DW_BUILD_NUMBER': _offset(engine, 'dwBuildNumber'),
        },
        'object_offsets': {
            'DW_HIGHEST_INDEX': _offset(client, 'dwGameEntitySystem_highestEntityIndex'),
            'OFF_NGC_LOCAL_PLAYER': _offset(engine, 'dwNetworkGameClient_localPlayer'),
        },
    }
    # Enforce the same exact field set/ranges used by the updater before returning.
    updater.manifest_values(json.dumps(manifest, separators=(',', ':')).encode(), schema_data)
    return manifest


def validate_buttons(data):
    doc = updater.load(data)
    if not isinstance(doc, dict) or not isinstance(doc.get('client.dll'), dict) or not doc['client.dll']:
        raise ValueError('buttons.json: ожидался непустой объект client.dll.')
    buttons = doc['client.dll']
    for name, value in buttons.items():
        if not isinstance(name, str) or not name:
            raise ValueError('buttons.json: некорректное имя действия.')
        updater.number(value, 0x40000000)
    return sorted(buttons)


def _validate_optional(files):
    result = {}
    if 'buttons.json' in files:
        result['buttons'] = validate_buttons(files['buttons.json'])
    if 'info.json' in files:
        info = updater.load(files['info.json'])
        if not isinstance(info, dict):
            raise ValueError('info.json должен быть JSON-объектом.')
        result['info_build_number'] = info.get('build_number')
    return result


def _validate_and_plan(root, files):
    schema = files['client_dll.json']
    updater.schema_values(schema)
    manifest = offsets_manifest(files['offsets.json'], schema)
    optional = _validate_optional(files)
    with tempfile.TemporaryDirectory(prefix='hachbob-bundle-check-') as directory:
        base = Path(directory)
        schema_path = base / 'client_dll.json'
        manifest_path = base / 'globals-manifest.json'
        schema_path.write_bytes(schema)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        profiles_path = None
        if 'ks_profiles.json' in files:
            profiles_path = base / 'ks_profiles.json'
            profiles_path.write_bytes(files['ks_profiles.json'])
        proposed, changes = updater.plan(root, schema_path, manifest_path, profiles_path)
    return manifest, optional, changes


def stage_files(root, files, ignored=()):
    root = Path(root).resolve()
    pointer = root / STAGE_POINTER
    if (root / updater.LOCK).exists():
        raise ValueError('Есть незавершённая транзакция; сначала восстанови её через patch_update.py --rollback.')
    if pointer.exists():
        raise ValueError('Уже есть ожидающий пакет. Примени его запуском start.bat или удали через patch_bundle.py --discard-staged.')
    manifest, optional, changes = _validate_and_plan(root, files)
    payload = {
        'client_dll.json': files['client_dll.json'],
        'globals_manifest.json': (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8'),
    }
    if 'ks_profiles.json' in files:
        payload['ks_profiles.json'] = files['ks_profiles.json']
    for name in ('buttons.json', 'info.json'):
        if name in files:
            payload[name] = files[name]
    token = 'package-' + uuid.uuid4().hex
    parent = root / STAGE_DIR
    parent.mkdir(parents=True, exist_ok=True)
    temporary = parent / ('.incoming-' + uuid.uuid4().hex)
    final = parent / token
    temporary.mkdir()
    try:
        for name, data in payload.items():
            (temporary / name).write_bytes(data)
        receipt = {
            'format': 'hachbob-staged-patch-v1', 'id': token,
            'files': {name: hashlib.sha256(data).hexdigest() for name, data in payload.items()},
        }
        (temporary / 'receipt.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
        os.replace(temporary, final)
        updater.atomic(pointer, (token + '\n').encode('ascii'))
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        if final.exists() and not pointer.exists():
            shutil.rmtree(final, ignore_errors=True)
        raise
    return {'id': token, 'changes': changes, 'optional': optional,
            'ignored': list(ignored), 'stage': final}


def _read_staged(root):
    root = Path(root).resolve()
    pointer = root / STAGE_POINTER
    if not pointer.exists():
        return None, None
    token = pointer.read_text(encoding='ascii').strip()
    if not re.fullmatch(r'package-[a-f0-9]{32}', token):
        raise ValueError('Ожидающий пакет имеет некорректный указатель; ничего не применено.')
    folder = (root / STAGE_DIR / token).resolve()
    if folder.parent != (root / STAGE_DIR).resolve() or not folder.is_dir():
        raise ValueError('Каталог ожидающего пакета отсутствует; указатель сохранён для диагностики.')
    receipt = updater.load((folder / 'receipt.json').read_bytes())
    if (not isinstance(receipt, dict) or set(receipt) != {'format', 'id', 'files'} or
            receipt.get('format') != 'hachbob-staged-patch-v1' or receipt.get('id') != token or
            not isinstance(receipt.get('files'), dict)):
        raise ValueError('Некорректная квитанция ожидающего пакета.')
    allowed = {'client_dll.json', 'globals_manifest.json', 'ks_profiles.json', 'buttons.json', 'info.json'}
    names = set(receipt['files'])
    if not {'client_dll.json', 'globals_manifest.json'} <= names or not names <= allowed:
        raise ValueError('Неверный набор файлов в ожидающем пакете.')
    data = {}
    for name, expected in receipt['files'].items():
        if not re.fullmatch(r'[a-f0-9]{64}', str(expected)):
            raise ValueError('Некорректный SHA256 в квитанции: ' + name)
        path = folder / name
        if path.parent != folder or not path.is_file() or path.stat().st_size > MAX_JSON:
            raise ValueError('Файл ожидающего пакета отсутствует или слишком велик: ' + name)
        data[name] = path.read_bytes()
        if hashlib.sha256(data[name]).hexdigest() != expected:
            raise ValueError('SHA256 не совпал: ' + name)
    files = {'client_dll.json': data['client_dll.json']}
    # Reconstruct the standard offsets JSON-equivalent inputs from the sealed manifest.
    manifest = updater.load(data['globals_manifest.json'])
    if not isinstance(manifest, dict) or manifest.get('format') != 'hachbob-offsets-v1':
        raise ValueError('Некорректный staged globals-manifest.')
    for name in ('ks_profiles.json', 'buttons.json', 'info.json'):
        if name in data:
            files[name] = data[name]
    return folder, (files, data['globals_manifest.json'])


def apply_staged(root=ROOT):
    root = Path(root).resolve()
    staged = _read_staged(root)
    if staged[0] is None:
        print('No staged patch; continuing with current installation.')
        return 0
    folder, (files, manifest_bytes) = staged
    if (root / updater.LOCK).exists():
        raise ValueError('Есть незавершённая транзакция; сначала откати её.')
    optional = _validate_optional(files)
    schema = files['client_dll.json']
    updater.schema_values(schema)
    # Exact manifest SHA binding and all numeric bounds are checked again at apply time.
    updater.manifest_values(manifest_bytes, schema)
    with tempfile.TemporaryDirectory(prefix='hachbob-staged-apply-') as directory:
        base = Path(directory)
        schema_path = base / 'client_dll.json'
        manifest_path = base / 'globals-manifest.json'
        schema_path.write_bytes(schema)
        manifest_path.write_bytes(manifest_bytes)
        profiles_path = None
        if 'ks_profiles.json' in files:
            profiles_path = base / 'ks_profiles.json'
            profiles_path.write_bytes(files['ks_profiles.json'])
        proposed, changes = updater.plan(root, schema_path, manifest_path, profiles_path)
    backup = updater.transaction(root, proposed)
    result = {'backup': str(backup), 'changes': changes, 'buttons_stored_not_applied': optional.get('buttons', [])}
    try:
        updater.atomic(folder / 'applied.json', (json.dumps(result, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
    finally:
        try:
            (root / STAGE_POINTER).unlink()
        except FileNotFoundError:
            pass
    print('Staged patch applied. PREVIEW ONLY; private modifier approval removed.')
    print('Backup:', backup)
    print('Constant changes:', len(changes))
    print('Rollback: py -3.11 patch_update.py --rollback "' + str(backup) + '"')
    if optional.get('buttons'):
        print('buttons.json was validated and archived, not mapped to HACHBOB hotkeys:', ', '.join(optional['buttons']))
    return 0


def discard_staged(root=ROOT):
    root = Path(root).resolve()
    pointer = root / STAGE_POINTER
    if not pointer.exists():
        print('No staged patch to discard.')
        return 0
    token = pointer.read_text(encoding='ascii').strip()
    if not re.fullmatch(r'package-[a-f0-9]{32}', token):
        raise ValueError('Некорректный указатель staged-пакета; удалять его автоматически небезопасно.')
    folder = root / STAGE_DIR / token
    if folder.exists():
        shutil.rmtree(folder)
    pointer.unlink()
    print('Staged patch discarded; installed files unchanged.')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--stage-list', type=Path, help='Developer: stage a JSON/ZIP selection, including an optional profiles file')
    actions.add_argument('--stage-list-user', type=Path, help='In-app updater: stage offsets while refusing ks_profiles.json')
    actions.add_argument('--apply-staged', action='store_true', help='Apply staged import on a clean next startup')
    actions.add_argument('--discard-staged', action='store_true', help='Discard a pending staged import')
    args = parser.parse_args(argv)
    try:
        if args.apply_staged:
            return apply_staged(ROOT)
        if args.discard_staged:
            return discard_staged(ROOT)
        list_path = args.stage_list or args.stage_list_user
        if not list_path:
            parser.print_help()
            return 0
        path_list = list_path.read_text(encoding='utf-8-sig').splitlines()
        paths = [Path(line.strip().strip('"')) for line in path_list if line.strip()]
        files, ignored = collect_files(paths, allow_profiles=args.stage_list_user is None)
        result = stage_files(ROOT, files, ignored)
        print('Recognized:', ', '.join(sorted(files)))
        print('Validated against the exact schema; constant changes:', len(result['changes']))
        if result['optional'].get('info_build_number') is not None:
            print('info.json build_number is dump metadata only:', result['optional']['info_build_number'])
        if result['optional'].get('buttons'):
            print('buttons.json validated and stored for reference (not used as HACHBOB hotkeys):',
                  ', '.join(result['optional']['buttons']))
        if 'ks_profiles.json' in files:
            print('Included profile file validated; application will force AUTO OFF and clear private modifier approval.')
        if ignored:
            print('Unrecognized/ignored files:', ', '.join(ignored[:20]))
        print('Staged for next clean start:', result['id'])
        print('Restart through start.bat after closing Dota, HACHBOB and other Python processes.')
        print('No installation files were changed by this selection step.')
        return 0
    except (ValueError, KeyError, TypeError, OSError, RecursionError, zipfile.BadZipFile) as exc:
        print('ERROR:', exc, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
