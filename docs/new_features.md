# docs/new_features.md

### email & dropbox / google drive / etc. auto-import
- add support for listening to a local directory or remote url
- support downloading & ingesting from email address (users email the address, programs downloads file, clears email on success)
- discord bot url-to-ingest support

### Automatic Retry on Failed Downloads / Transcriptions
- configurable amount of retries per job in settings

### Always On Mode:
- launch back end on startup, closing app only closes front end

### Summary & Genre field
- optional step: post-transcription ai-generated transcript
- in the case of videos of substantial length, set a transcript segment limit
- use youtube metadata or use transcript summary to group item into queryable genres
- in settings, allow for custom summary instrcutions (set by path)

### filter tags for search
- add a set of reserved flags for search filtering, add semicolon argument splitting, add not-flags, add wildcard flags 
  Examples:
  1. title field contains 'slay the spire' and transcription contains both 'the' and 'of'
    'TITLE slay the spire; the & of'
  2. creator field contains 'north' and transcription contains either 'how' or 'where'
    'CREATOR north; how | where'
  3. genre field does not contain 'comedy' and transcription contains anything
    'GENRE! comedy; *'
  4. any field contains 'north' and transcription contains anything
    '* north; *'
  5. title field contains anything and transcription doesn't contain 'wow'
    '* *; TS! wow'
  6. title field contains 'milk' and not 'water' and transcript doesn't contain either 'soda' or 'teeth'
    'TITLE milk; TITLE! water; TS! soda | teeth'
  7. transcript doesn't contain both 'horse' and 'bear' (can contain either, just not both)
    'TS! horse & bear'

  as you can see, TS (transcript segment) is assumed unless a reserved all-caps field is provided as first word of a semicolon broken string
