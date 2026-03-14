# docs/new_features.md

### email & dropbox / google drive / etc. auto-import
- add support for listening to a local directory or remote url
- support downloading & ingesting from email address (users email the address, programs downloads file, clears email on success)
- discord bot url-to-ingest support

### Always On Mode:
- launch back end on startup, closing app only closes front end

### Summary & Genre field
- optional step: post-transcription ai-generated transcript
- in the case of videos of substantial length, set a transcript segment limit
- use youtube metadata or use transcript summary to group item into queryable genres
- in settings, allow for custom summary instrcutions (set by path)

### multiple fields visualized in search
- default header fields: video, creator, length
- draggable position / divisions
- set the sort hierarchy via roman-numeral nerd font char next to field title in header row
- add / remove fields to header in order to choose what to visualize

## workflow UI

1. when killing a job, we highlight the worker with the accent color in the visualization
2. when unqueue a video, we highlight the filename with the accent color in the visualization

## Video Search Preview pane
1. add a preview pane, just like we have with browse, with a thumbnail, name, creator, and description.

## player UI

1. play/pause button (nerd font text char)
2. playback speed setting (default 1, accept text or option from drop-down)
3. toggleable metadata pane (below player, on the left, sticks to the player on dynamic split)
metadata pane looks like this:
+------------------------------------------+
video player
...
...
...
...
...
+------------------------------------------+
closed captions (via transcription)
+------------------------------------------+
 [x1.5] [###############.........]
+------------------------------------------+
  title
  creator
  genre
  description
  ...
  ...
+------------------------------------------+
