"""Install this project's ComfyUI extension or prepare its portable workflow; no network calls."""
import argparse
import datetime
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def selected_files(root):
    return [p for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts
            and p.suffix != '.pyc' and p.name not in ('api_key.txt', '.env', 'auth.json')]


def copy_preserving_keys(source, target, backups):
    for src in selected_files(source):
        relative = src.relative_to(source)
        dst = target / relative
        if dst.exists() and dst.read_bytes() != src.read_bytes():
            backup = backups / relative
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, backup)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def prepare(photos, destination):
    source = ROOT / 'workflows/fabric_lookbook_v7_filled.json'
    workflow = json.loads(source.read_text(encoding='utf-8'))
    photo_dir = Path(photos).resolve()
    folder = next(n for n in workflow['nodes'] if n['type'] == 'FabricMaterialFolder')
    values = folder['widgets_values_named']
    main = values['flat_filename']
    files = [main] + [p.replace('{stem}', Path(main).stem) for p in values['extra_filenames'].splitlines() if p.strip()]
    if not all((photo_dir / p).is_file() for p in files):
        raise ValueError('Missing selected example photos; select the folder containing the complete group.')
    values['folder_path'] = str(photo_dir)
    folder['widgets_values'][0] = str(photo_dir)
    for node in workflow['nodes']:
        if node['type'] == 'FabricGPTImage2':
            assert node['widgets_values_named']['openai_api_key'] == ''
            assert node['widgets_values_named']['api_key_file'] == ''
    destination = Path(destination).resolve()
    if destination == source.resolve():
        raise ValueError('Keep the portable template; write the machine-specific workflow to another path.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        shutil.copy2(destination, destination.with_name(destination.stem + '.before-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.json'))
    destination.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding='utf-8')
    return str(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'install', 'install-skill'])
    parser.add_argument('--photos-dir', default=str(ROOT/'portable/references/material-1'))
    parser.add_argument('--workflow-out', default=str(ROOT/'local/fabric_lookbook_v7_filled.json'))
    parser.add_argument('--comfy-root', help='Directory containing ComfyUI main.py and custom_nodes')
    parser.add_argument('--skills-dir', default=str(Path.home()/'.codex/skills'))
    args = parser.parse_args()
    backup = ROOT / 'local/backups' / datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    if args.action == 'install-skill':
        target = Path(args.skills_dir).expanduser().resolve() / 'fabric-scene-studio'
        copy_preserving_keys(ROOT/'skills/fabric-scene-studio', target, backup/'skill')
        print(json.dumps(dict(skill=str(target), api_calls=0), ensure_ascii=False))
        return
    if args.action == 'install':
        if not args.comfy_root:
            parser.error('--comfy-root is required; no automatic installation target')
        comfy = Path(args.comfy_root).resolve()
        if not (comfy/'main.py').is_file() or not (comfy/'custom_nodes').is_dir():
            raise ValueError('Select the actual ComfyUI directory, not its Desktop application folder.')
        # Validate template/photo paths before changing installed code.
        output = prepare(args.photos_dir, args.workflow_out)
        copy_preserving_keys(ROOT/'custom_nodes/fabric_prompt_tools', comfy/'custom_nodes/fabric_prompt_tools', backup/'nodes')
        print(json.dumps(dict(installed=True, workflow=output, api_calls=0,
                              next='Use the ComfyUI environment to install requirements if missing, restart ComfyUI, and open this workflow. Existing key files were not read or replaced.'), ensure_ascii=False))
    else:
        print(json.dumps(dict(workflow=prepare(args.photos_dir,args.workflow_out), api_calls=0),ensure_ascii=False))


if __name__ == '__main__':
    main()
