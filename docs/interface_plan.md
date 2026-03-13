# interface_plan.md


## Visual Style

The current Terminal-Inspired Aesthetic is Perfect.
We should retain the appearance as much as possible as we add new features.

## AI Integration

We should attempt to ship a basic lightweight Ollama AI alongside the program.
This will be the default LLM for the AI Interface.
The project still should provide full API coverage to any AI the user uses.

## Global Controls

These Controls should always be available in the GUI.
Only 1 Popup-Window can be Open at a Time.
If the Current Popup-Window is Issued via Keybind, Close the Popup and Launch Nothing
If the Current Popup-Window is Not the Issued Popup-Window, Close it and Then Launch the New Popup

#Ctrl-P: Command Menu
#Ctrl-N: Ingest
#Ctrl-I: Workers
#Ctrl-O: Open File
#Ctrl-F: Finder
#Ctrl-A: AI
#Ctrl-S: Settings
#Ctrl-Q: Close Program

## Modes / Modal Controls

1. Player (Default)
```
Primary-Window Interface for Watching Videos.
Provides a VLC Playback Window, Video Details Window, & a Filterable Transcript Log
```
  Controls:
    #Up/Down/Home/End/PgUp/PgDown: Navigate Filtered Transcript List (While Log Visible)
    #Left/Right: Move Filter Query Cursor Left/Right (While Log Visible)
    #AlphaNumberic/Space: Add Character to Filter Query (While Log Visible)
    #Enter: Moves Playback to the StartTime of the Cursored Transcription
    #Ctrl-Space: Pause/Resume Playback
    #Ctrl-Left/Right: Jump Playback Forward/Backward 5 Seconds
    #Ctrl-Up/Down: Jump Playback to Next/Previous Filtered Transcript
    #Ctrl-T: Toggles Visability of the Transcript Log
    #Ctrl-D: Toggles Visability of the Details Window
    #Ctrl-S: Toggles Skim Mode (Only Play Back Filtered Entries)

2. Command Menu (Ctrl-P)
```
Popup-Window Menu Listing GUI Commands.
Provides Choices as a Filterable List.
```
  Controls:
    #Up/Down/Home/End/PgUp/PgDown: Navigate Filtered Command List
    #Left/Right: Move Filter Query Cursor Left/Right
    #AlphaNumeric/Space: Add Characters to Filter Query
    #Enter: Runs Cursored Command
    #Escape: Close Popup-Window

3. Workers (Ctrl-I)
```
Popup-Window Interface for Ingest Workers.
Provides a View of Worker State & a Cursor-Navigated List of Commands.
```
  Controls:
    #Up/Down/Home/End/PgUp/PgDown: Navigate Command List
    #Enter: Open List of Workers. Hit Enter on Cursored worked to Run Command on Worker.
    #Escape: Close Popup-Window

  Commands:
    #Create: Create a New Ingest Worker
    #Retire: Close a Existing Ingest Worker
    #Assign: Assign a Task to an Ingest Process
    #Pause:  Pause an Ingest Process
    #Resume: Resume a Paused Ingest Process
    #Cancel: Cancel an Ingest Process (Optionally Clear Downloaded Files)

  Assignable Tasks:
    #Ingest: Full Ingest Cycle for a Local File or URL (Download, Transcribe, Register)
    #Download: Download a File from URL or Filepath (Copy to Media Directory)
    #Transcribe: Transcribe a Video from Filepath
    #Register: Register a Video / Transcription File Pair vie Filepaths
    #Retranscribe: Redo Transcription for a Video in the Database

4. Ingest (Ctrl-N)
```
Popup-Window Interface for Ingesting Content.
Provides a Text Input Line and a Cursor-Navigated List of Commands.
```
  Controls:
    #Up/Down/Home/End/PgUp/PgDown: Navigate Command List
    #Left/Right: Move Text Input Cursor
    #AlphaNumeric/Space: Add Characters to Text Input
    #Enter: Run Command with Current Text Input as Argument
    #Escape: Close Popup-Window

  Commands:
    #Ingest: Full Ingest Cycle for 1+ Youtube Videos via URL
    #Browse: Provides a Filterable List of Youtube Videos (Most Recent) via Youtube Account Name
    #Subscribe: Sets up Auto-Download for a Provided Youtube Account Name

5. Open Video (Ctrl+O)
```
Popup-Window Interface for loading Database Entries into the Player.
Provides a Filterable List of Database Entries by Video Name + Creator.
```
  Controls:
    #Up/Down/Home/End/PgUp/PgDown: Navigate Filtered File List
    #Left/Right: Move Filter Query Cursor Left/Right
    #AlphaNumberic/Space: Add Character to Filter Query
    #Enter: Opens Cursored Database Entry into the Player
    #Escape: Close Popup-Window

5. Finder (Ctrl+F)
```
Popup-Window Interface for loading Database Entries into the Player.
Provides a Filterable List of Database Entries with Matching Transcript Content.
```
  Controls:
    #Up/Down/Home/End/PgUp/PgDown: Navigate Filtered File List
    #Left/Right: Move Search Query Cursor Left/Right
    #AlphaNumberic/Space: Add Character to Search Query
    #Enter: Opens Cursored Database Entry into the Player
    #Escape: Close Popup-Window

6. Agent (Ctrl-A)
```
Toggleable Popup-Window Interface for Prompting a Configured AI Agent.
Provides a Cursor-Navigated Chat Feed with the AI Agent & a Text Input Line.
```
  Controls:
    #Up/Down/Home/End/PgUp/PgDown: Navigate Chat Feed
    #Left/Right: Move Text Input Cursor Left/Right
    #AlphaNumberic/Space: Add Character to Text Input
    #Enter: Send Text Input Line Contents as a Prompt to LLM
    #Escape: Close Popup-Window

7. Settings (Ctrl-S)
```
Popup-Window Interface for adjusting Program Configuration.
Provides a Cursor-Navigated List of Program Settings.
```

  Controls:
    #Up/Down/Home/End/PgUp/PgDown: Navigate Command List
    #Enter: Moves cursor to Text Field for Cursored Setting. If in Text Field: Submits Setting Change.
    #Escape: Close Popup-Window. If in Text Field: Exits without Submitting/Saving New Value.

