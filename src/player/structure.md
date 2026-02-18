## ALOG Project Structure

### Concept
Simple to use tool for content creators which downloads, transcribes, logs, and queries videos.
An ideal verion of this tool works seamlessly with AI Tools, expanding Agent Understanding of Video.

### Goals
CLI, TUI, & GUI all work with the same core API.
App is equally useful to Human, Solo-Agent, and Hybrid Workflows.

### TODO
- [ ] Rewrite into more organized codebase
- [ ] Polish API Endpoints with short & longform argument flags
- [ ] Polish GUI UI to fit both Keyboard & Mouse based workflows
- [ ] Add Youtube Channel Subscription
- [ ] Add Optional Storage Lifetimes (keep transcript, video, or both)
- [ ] Add Config editable from file or within GUI
- [ ] Add Simple Editor using ffmpeg to export new clips
- [ ] Add DB Management from within GUI, TUI, CLI
- [ ] Add Trends Analysis 

### Actions
1. download video
2. transcribe video
3. enter video, transcription, or pair into database (allow URL as video link)
4. query single video transcriptions
5. query full db for video transcriptions
6. export video clips via timestamps
7. subscribe to youtube channel (auto-downloader daemon)
8. generate trends analysis via transcript

