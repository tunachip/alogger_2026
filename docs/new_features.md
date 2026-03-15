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

## workflow UI

1. when killing a job, we highlight the worker with the accent color in the visualization
2. when unqueue a video, we highlight the filename with the accent color in the visualization

