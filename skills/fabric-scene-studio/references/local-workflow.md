# ComfyUI project layout

Resolve paths from the user's repository, not a particular machine. Node sources: custom_nodes/fabric_prompt_tools. Template: workflows. Standalone runner: portable/fabric_scene.py. Installation: tools/install.py with explicit --comfy-root and --photos-dir.

The v7 graph has24 nodes: folder3; captions4/5/25/28; rules6; scenes7/10/13/16/19; generators8/11/14/17/20; saves9/12/15/18/21; preview27. Check the actual graph before using IDs.

Original references travel via original_refs; padded preview batches are not the actual uploads. Captions describe each photo, Faithfulness stores shared rules and ScenePreset controls outfit/subject/photography. Override fields take precedence over presets. Preserve edits and modes. Python changes require a safe restart after checking the queue; prompt edits do not.

Public repositories contain no customer photos or cache. Users supply their own. The portable runner and canvas are explicitly synchronized, not live mirrors. Keep keys local and preserve existing api_key.txt during installation.

Shared transparency node29 follows Faithfulness and supplies references/prompts to the scenes; preview30 displays its crop. Public defaults to text mode without a private chart path.
