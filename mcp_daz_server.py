import json
import os
import subprocess
import sys
import time

DAZ_SCRIPT_PATH = os.environ.get('DAZ_SCRIPT_PATH', 'C:\\knosso\\Daz\\scripts')
DAZ_EXE = os.environ.get('DAZ_EXE', 'C:\\Program Files\\DAZ 3D\\DAZStudio4\\dazstudio.exe')

def run_daz_script(script_name, args=None):
    script_path = os.path.join(DAZ_SCRIPT_PATH, script_name)
    if not os.path.exists(script_path):
        return {'status': 'error', 'message': f'Script not found: {script_path}'}

    # Prepare arguments for DAZ Studio
    daz_args = [f'-file "{script_path}"']
    if args:
        # For passing arguments to the DAZ script, we can write them to a temp JSON file
        # and pass the path to that file as an argument to the DAZ script.
        # The DAZ script then reads this JSON file.
        temp_params_path = os.path.join(DAZ_SCRIPT_PATH, 'temp_params.json')
        with open(temp_params_path, 'w') as f:
            json.dump(args, f)
        daz_args.append(f'-args "{temp_params_path}"')

    # Construct the command to run DAZ Studio
    command = [DAZ_EXE] + daz_args
    print(f"Running DAZ Studio command: {' '.join(command)}")

    try:
        # Use subprocess.Popen for non-blocking execution
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, stderr = process.communicate(timeout=60) # Add a timeout for the process

        if process.returncode != 0:
            return {'status': 'error', 'message': f'DAZ Studio exited with error code {process.returncode}', 'stdout': stdout, 'stderr': stderr}
        else:
            return {'status': 'success', 'stdout': stdout, 'stderr': stderr}
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        return {'status': 'error', 'message': 'DAZ Studio command timed out', 'stdout': stdout, 'stderr': stderr}
    except Exception as e:
        return {'status': 'error', 'message': f'Failed to run DAZ Studio: {e}'}

def load_scene(scene_path):
    print(f"Attempting to load scene: {scene_path}")
    return run_daz_script('load_scene.dsa', {'scene_path': scene_path})

def render_scene(output_path, width=1920, height=1080):
    print(f"Attempting to render scene to: {output_path}")
    return run_daz_script('render_scene.dsa', {'output_path': output_path, 'width': width, 'height': height})

def set_pose(figure_name, pose_file):
    print(f"Attempting to set pose for {figure_name} using {pose_file}")
    return run_daz_script('set_pose.dsa', {'figure_name': figure_name, 'pose_file': pose_file})

def handle_request(request_str):
    try:
        request = json.loads(request_str)
        tool_name = request.get('tool_name')
        args = request.get('args', {})

        if tool_name == 'load_scene':
            result = load_scene(args.get('scene_path'))
        elif tool_name == 'render_scene':
            result = render_scene(args.get('output_path'), args.get('width'), args.get('height'))
        elif tool_name == 'set_pose':
            result = set_pose(args.get('figure_name'), args.get('pose_file'))
        else:
            result = {'status': 'error', 'message': f'Unknown tool: {tool_name}'}
    except json.JSONDecodeError:
        result = {'status': 'error', 'message': 'Invalid JSON request'}
    except Exception as e:
        result = {'status': 'error', 'message': f'Server error: {e}'}
    return json.dumps(result)

if __name__ == '__main__':
    if '--stdio' in sys.argv:
        print("MCP DAZ server started in stdio mode.")
        print(f"DAZ_SCRIPT_PATH: {DAZ_SCRIPT_PATH}")
        print(f"DAZ_EXE: {DAZ_EXE}")
        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    break
                response = handle_request(line.strip())
                sys.stdout.write(response + '\n')
                sys.stdout.flush()
            except Exception as e:
                sys.stderr.write(f"Error in stdio loop: {e}\n")
                sys.stderr.flush()
    else:
        print("This script is intended to be run as an MCP server in stdio mode. Use --stdio argument.")
