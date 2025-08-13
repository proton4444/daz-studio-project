import json
import sys

def send_request(tool_name, args):
    request = {
        'tool_name': tool_name,
        'args': args
    }
    sys.stdout.write(json.dumps(request) + '\n')
    sys.stdout.flush()
    response_line = sys.stdin.readline()
    return json.loads(response_line)

if __name__ == '__main__':
    # Example: Load a scene
    # response = send_request('load_scene', {'scene_path': 'D:/My Lybrary/Scenes/deviant.duf'})
    # print(f"Load scene response: {response}")

    # Example: Render a scene
    response = send_request('render_scene', {'output_path': 'C:/Users/knoss/Desktop/rendered_scene.png', 'width': 800, 'height': 600})
    print(f"Render scene response: {response}")

    # Example: Set a pose
    # response = send_request('set_pose', {'figure_name': 'Genesis 8 Female', 'pose_file': 'D:/My Lybrary/Poses/some_pose.duf'})
    # print(f"Set pose response: {response}")
