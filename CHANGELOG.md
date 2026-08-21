# Changelog

All notable user-facing changes to Waves are documented here, newest first.
Each release section becomes the GitHub Release notes for that version, so keep
bullets short, objective, and written for end users.

Format per release: `## vX.Y.Z (YYYY-MM-DD)` followed by any of the subheadings
**Added**, **Changed**, **Fixed**, **Removed**, each a bullet list, always in
that order (a test enforces it). Section headings and their bullets carry a
leading emoji accent (for example ✨ Added, 🔧 Changed, 🐛 Fixed). Changes land
under **Unreleased** as they are made; cutting a release renames that section
to the new version.

A bullet is written on one line, however long it runs. GitHub renders a Release
body with every newline turned into a line break, so a bullet wrapped across
source lines arrives on the Releases page broken mid-sentence, one ragged
fragment per source line. A test enforces it.

A bullet that closes a reported issue names it in full and links to it:
`([issue #11](https://github.com/iamprivacy/Waves/issues/11))`, never a bare
`(#11)`. These notes are read outside the repo (release emails, the website,
package managers), where a bare number is neither a link nor obviously an
issue. A test enforces it.

## 🗂️ v0.1.23 (2026-08-20)

### 🔧 Changed

- ⚙️ The "When an album has several editions" setting now spells out that only "Best of both" works on its own: the other three choices take effect only with "Most-complete edition only" switched on ([issue #26](https://github.com/iamprivacy/Waves/issues/26)).

### 🐛 Fixed

- 🔊 With "Download Dolby Atmos" switched off, "Download discography" no longer saves the Dolby Atmos edition of an album alongside the regular one ([issue #26](https://github.com/iamprivacy/Waves/issues/26)).

## 🗂️ v0.1.22 (2026-08-19)

### 🔧 Changed

- 🔊 The Dolby Atmos version of an album or song now appears as its own entry in search results, next to the regular version, marked ATMOS SPATIAL. It used to be folded into the regular entry, so you could not see it or choose it. The same ATMOS mark is used everywhere a quality is shown, in place of a quality tier that did not apply.
- 📊 A download button's progress bar now fills the whole button with a denser grid of blocks. Hover the button (or the cover, on a Browse card) to see the percentage. The queue shows progress too: a percentage for a single song, and how many songs are done for an album, playlist or mix.
- 📊 The queue's per-row progress bar uses the same denser grid.
- ⚙️ "Best of both" no longer needs "Most-complete edition only" switched on, and its setting is always visible in Settings. It works on its own, both when you save a single album and when you save a whole discography.
- 🧩 "Best of both" now works on albums that also have a clean or explicit twin. It builds the version you asked for and never mixes the two: songs are only ever taken from editions of the same kind.
- 📚 Open a queued album, playlist or mix in the queue and the songs you already have are marked straight away, each with the quality of the copy you hold, in the same green (Waves wrote it) or gold (matched by tags) as the download button. They used to be marked one at a time as the download reached them, and showed no quality at all.
- 📋 A downloading playlist or mix opens in the queue like an album does: hover the row to peek, click for the per-track list with live progress and quality. Only album rows could open before. A playlist longer than 500 tracks lists the first 500 and says how many more it holds.
- 🔮 The queue now shows the quality a download will really get before it starts: the tier you asked for, capped by what TIDAL lists for that release and track.

### 🐛 Fixed

- ⏱️ The "Download delay" setting works. It had no effect either way before: an album always paused a few seconds between songs even with the delay switched off, and a single song never paused even with it switched on.
- 🎛️ Changing a setting part-way through a download no longer affects the songs still to come in it. Answering a question about your download folder, or ticking "Don't ask again", could leave the rest of an album without its finishing step, so those songs showed the wrong length, or could fail them outright.
- 📊 In the queue, each song's progress bar shows that song's progress. On a release with a long list of artists every song showed the same figure, and the album's overall bar could run ahead of the truth and stay there.
- 🔐 A connection problem when Waves starts no longer signs you out. TIDAL being busy, a server error, or a wifi sign-in page at a hotel or airport used to clear your sign-in for good, so you had to sign in again. Waves now keeps you signed in and tries again.
- 🔊 With Dolby Atmos downloads switched on and audio quality set to Lossless or Max, saving an album again no longer downloads its Atmos songs a second time, and the album shows as downloaded. Before, those songs were downloaded again every time and the button stayed on DOWNLOAD.
- 🔊 Dolby Atmos songs save straight after you sign in. On a first launch, or after signing out and back in, every Atmos song in every download failed until you quit Waves and opened it again.
- 🔊 Dolby Atmos songs now save in Dolby Atmos. They had been saving as ordinary stereo files instead, and were never marked ATMOS SPATIAL. If you already have one of those stereo copies, Redownload it to get the Dolby Atmos version.
- 🖼️ The progress bar on a Browse card (an album, playlist or mix cover) has its green outline back; since v0.1.18 it painted over its own edge.
- 🎚️ In the queue's per-track list, a track's quality no longer disappears for a few seconds when it says COMPLETED.
- 🎧 Changing the audio quality no longer affects downloads already queued or in progress. They finish at the quality they started at, and the new choice applies to what you queue next.
- 🚀 The Browse landing is ready sooner after launch, and cover art no longer holds up the interface.
- 🏁 A "best of both" album is no longer marked failed when TIDAL has withheld one of its songs. It finishes with the songs it could get and the status line names what was left out, the same as any other album.
- ⚖️ "Best of both" now builds a merged album only when that gets you better audio at the quality you chose. It used to build one whenever another edition listed a higher quality, even where your setting meant every song downloaded exactly the same.
- 🎵 "Best of both" no longer takes every song from an edition that happens to hold one higher-quality track, and never takes a song at a lower quality than the edition it started from.
- 💾 An album saved as a "best of both" shows as downloaded when you reopen Waves.
- ♻️ Saving an album again after a "best of both" replaces the files instead of leaving a second copy of each borrowed song beside them. This now works for a library saved by an older version too, where the album's folder was named a little differently: the higher-quality songs land in the folder you already have instead of the save quietly skipping them.
- 🔁 When Waves cannot compare an album's editions, it now says so and you can try again. It used to save the plain album and stop offering "best of both" for that album until you restarted.
- 📃 An album saved as a "best of both" gets its playlist file (.m3u8) when that setting is on, the same as any other album. It was the one kind of album that never did, and saving it again did not add one.
- 📁 Saving an album adds to the album folder you already have, even when that folder keeps a name from before v0.1.17 (the doubled space left where an illegal character was removed). On the file naming Waves ships with, the songs went into a second, tidier-named folder beside it, so the album ended up saved twice.
- 📚 Changing "Bulk downloads skip what you have" no longer affects downloads already queued. They run with the setting they were queued with, and the new choice applies to what you queue next, the same as the audio quality.
- 🖼️ The Preview and Download buttons that appear when you point at a cover in Browse now fit on the cover. On an album you had already saved the second one reads DOWNLOADED, which was wide enough to be cut off at both ends.

## 🗂️ v0.1.21 (2026-08-15)

### 🐛 Fixed

- 🎼 Albums whose tracks TIDAL marks as not streamable now save the tracks your account can play. Some editions (commentary or album-experience versions) carry that flag on every track even though TIDAL's own apps still play them, and Waves was refusing all of them up front, so an album could fail entirely when most of it was available to you. Waves now lets TIDAL itself decide, track by track: what TIDAL serves to your account is saved, and nothing more ([issue #25](https://github.com/iamprivacy/Waves/issues/25)).
- 🚫 A track TIDAL genuinely will not serve is now marked UNAVAILABLE instead of failed, and the rest of the album still finishes. Such a track used to be reported as a failure with a retry that could never work, and a whole album of them turned red ([issue #25](https://github.com/iamprivacy/Waves/issues/25)).

## 🗂️ v0.1.20 (2026-08-15)

### ✨ Added

- 🎧 Every queue row now shows its audio quality: the quality being fetched, then the quality that actually arrived, or MIXED when an album's tracks landed at different ones. Expanded tracks each show their own, so a release that arrives below the quality you asked for is visible instead of silent.

### 🔧 Changed

- 📋 Each track in an expanded album now states its outcome in words: QUEUED while waiting, DOWNLOADING with live progress, FINISHING while the file is tagged and moved into place, then COMPLETED, IN LIBRARY for a track you already own, or FAILED in red.
- 📐 The download queue drawer is wider and can be resized by dragging its left edge. The width you choose is remembered.
- 📚 A finished download only says IN LIBRARY when your library actually contains it. If downloads land outside your library folder, the done button says DOWNLOADED instead, and moving the files in flips it to IN LIBRARY on the next scan.
- 🖼️ A downloaded album's artwork keeps its Preview button: the download half now shows DOWNLOADED with a checkmark instead of replacing the whole control. It stays remembered across restarts, and clicking it asks whether to redownload; confirming re-fetches every track and replaces only the copies Waves itself wrote.
- ⚡ The download queue stays responsive through long batches: finished rows no longer pile up for the whole session, and progress updates cost far less. Nothing is forgotten: what you downloaded is recorded independently of the queue ([issue #24](https://github.com/iamprivacy/Waves/issues/24)).

### 🐛 Fixed

- 📚 Library detection no longer treats a song you own from one album as owned on every other album. Bulk downloads stopped silently leaving those tracks out of an album you asked for, and a compilation reusing recordings you hold now shows the gold MAYBE IN LIBRARY badge instead of a confident green one ([issue #24](https://github.com/iamprivacy/Waves/issues/24)).

### 🗑️ Removed

- 🔁 The "Skip songs you already have" setting is gone: owning a song on a single or another edition could leave a hole in an album you explicitly asked for, so an album you ask for now always arrives complete. Skip existing still skips files already in place, and library detection still skips a whole release you already hold ([issue #18](https://github.com/iamprivacy/Waves/issues/18), [issue #24](https://github.com/iamprivacy/Waves/issues/24)).

## 🗂️ v0.1.19 (2026-08-13)

### ✨ Added

- 📚 New, experimental: point Waves at your music library (Settings, Library) and albums, artists and tracks you already own wear an IN LIBRARY pill wherever they appear, colour-coded by the quality you hold and one click from the matching folder. The feature is off by default (nothing is scanned until you turn it on and save), and the files you already have are only ever read, never modified, moved or renamed. Clicking the pill opens that album's folder in Finder or Explorer, so a downloaded album is one click from the file manager ([issue #23](https://github.com/iamprivacy/Waves/issues/23)).
- 🏷️ The scan identifies your albums by their tags, so it recognises a library Waves did not create, whatever your folders are called. A multi-disc set counts as one album, edition names match by meaning ("Deluxe Edition" and "Deluxe Version" are one edition, "(2011 Remaster)" matches "(Remastered 2011)") while a live or acoustic release never matches the studio cut, and a folder short a track is never called complete. Real-world tag noise is absorbed too: accents match plain spellings (a library tagged Bjork finds Björk), "&" and "and" are one word, "The Beatles" and "Beatles" are one artist, featuring credits match however they were written, and one mis-tagged stray file no longer hides a whole album. Rescans re-read only what changed, and every folder you have scanned keeps its own saved index.
- ⏱️ Play length is part of the match: an undated copy whose track count and total seconds agree with the release is confirmed instead of being left as an unproven "?", a same-count copy minutes apart is recognised as a different recording, and a single track is confirmed by its own seconds. Seconds outrank the year tag, so a remaster tagged with the original album's year still matches.
- 🔎 An optional MusicBrainz check (Settings, Library, off by default) confirms matches the scan cannot settle on its own. It sends artist and album titles to musicbrainz.org, one request per second, and caches the answers locally.
- 🟡 Download buttons say what the scan found: green ALBUM IN LIBRARY for a complete copy it can confirm, gold MAYBE IN LIBRARY for one it cannot, cyan PARTIALLY IN LIBRARY for a copy you hold part of (a click fetches the rest). Single tracks say the same in their own words. Every one of them stays clickable, naming the matched folder with Download anyway behind it, because a tag match is a recognition, not a receipt.
- 🖼️ Browse cards carry the same verdict on the artwork, without waiting for a hover, and clicking a claimed one explains the match exactly as the full button does.
- ⏭️ Bulk downloads skip what your library already has (on by default while the scan is on, its own toggle on the Library card): a discography leaves out matched albums, an album or playlist skips its matched tracks, and the status line counts what was left out. A single track click always downloads, and best of both merges stay complete.
- 🟢 Done labels now name what they are about: ALBUM IN LIBRARY, TRACK DOWNLOADED, VIDEO IN LIBRARY.
- 🎬 A video you have downloaded shows a DOWNLOADED mark on its thumbnail.
- 🎤 Artists say what you hold of theirs: an artist in your library wears IN LIBRARY across the lower edge of their picture, with how many albums and songs you own, on search results, browse shelves and your followed artists alike. The artist's own page says the same under their name, above Download discography.
- 🔁 Skip songs you already have (Settings, Downloads, off by default): a song is skipped when the same recording already sits in another album's folder under that artist, so a deluxe edition downloaded after the standard one fetches only what you are missing. Matching uses the recording's ISRC code, so a live take, an alternate version or a re-recording is never mistaken for the same song. Best of both merges ignore the setting, since their job is one complete album ([issue #18](https://github.com/iamprivacy/Waves/issues/18)).
- 🔁 Failed downloads gather in their own Failed section of the queue drawer, and its header offers RETRY ALL: one click retries every failed row instead of hunting each one down ([issue #18](https://github.com/iamprivacy/Waves/issues/18)).

### 🔧 Changed

- 🧹 Each queue section clears itself: a CLEAR chip rides the Completed, Failed and Queued headers, and the footer keeps a single CLEAR ALL. Clearing never interrupts a download that is already running, and failed rows are only ever cleared from their own header ([issue #18](https://github.com/iamprivacy/Waves/issues/18)).
- ⏸️ The queue's PAUSE button turns gold while downloads run and becomes a green RESUME once paused.
- 🎞️ Download buttons roll between states instead of snapping, on the same belt browse uses for its live swaps. Turning off "Hover controls slide in" in Settings stills it.
- 📋 The search bar's paste button now runs the search too, instead of leaving the term in the box waiting for Enter. Ctrl+V (Cmd+V on Mac) still only fills the box, so a term can be edited before searching ([issue #18](https://github.com/iamprivacy/Waves/issues/18)).
- 📜 The first-run terms are clearer about what Waves is and what you take on by using it: a personal, non-commercial tool (the word "educational" is gone), your use may breach TIDAL's own Terms of Service and you accept that risk, liability is limited as the AGPL-3.0 sets out, and you agree to indemnify the project. They now carry a version stamp, so a later revision can ask only the people who saw an older one.
- 📏 The first-run terms card scrolls inside itself, so the checkbox and button stay reachable at the smallest window Waves allows.

### 🐛 Fixed

- 🎶 A downloaded playlist's file now lists its songs in the order TIDAL plays them, instead of the order the file names happened to sort, so a media server like Jellyfin plays it back as it was made. It is also written as .m3u8, the name the setting always promised; an existing .m3u file keeps its name and keeps receiving updates ([issue #22](https://github.com/iamprivacy/Waves/issues/22)).
- 🌐 A library folder on a NAS is scanned by a small crew of threads instead of sixteen at once, so a first scan can no longer stall a struggling SMB mount. A library on a local disk scans at full speed as before.
- 🔌 The library gets its share back on its own: macOS quietly ejects idle network volumes, and a library folder on one read as missing until you navigated to the share in Finder by hand. Every scan now asks macOS to mount it back first, the same way the download folder already reconnects, so a launch, the hourly re-check or one press of RESCAN restores the library without a trip to Finder.
- 🎼 An album whose songs all share one title keeps every one of them when Waves is allowed to replace files ("Skip download if file already exists" off, or a re-download upgrading quality). Songs downloading at the same moment could aim at one file name, so a six-track album downloaded three at a time ended up as four files, a different four every run ([issue #19](https://github.com/iamprivacy/Waves/issues/19)).
- 🪟 The "Exit while downloading?" prompt is no longer hidden behind the queue drawer, which used to leave the window looking stuck.
- ⏳ Library badges arrive with the page they belong to instead of a second later: your saved library index is now read before the scan starts rather than behind it, and a search waits for that first answer. A badge the running scan turns up while you are looking at the page still appears, now fading in rather than snapping on.
- 🖼️ A page of search results finishes building before it is shown, so the last rows no longer land after the page has already appeared.
- 📜 The terms you have to agree to are the first thing you see: the launch animation now fades straight into the agreement, and the rest of Waves is neither shown nor usable until you accept it.

### 🗑️ Removed

- ✂️ The unused audio decryption path is gone: TIDAL serves standard MPEG-DASH streams for every quality Waves requests, so it had no job to do. A stream that somehow arrives encrypted now stops with a clear error instead of leaving an unplayable file in your library.

## 🗂️ v0.1.18 (2026-08-09)

### ✨ Added

- ❌ A queued download can be called off from the button that queued it. The download button carries an ✕ beside QUEUED while a click is waiting its turn: pressing it drops that item from the queue, without opening the queue drawer to find the same row. The button goes back to offering the download.
- 🔣 Per-character stand-ins (Settings, File organization, under the illegal-character stand-in). One stand-in for every rejected character reads badly on the ones that carry meaning, so each character a file name cannot hold can now be given its own: " · " for ":" writes "Rarities Edition · Live" instead of "Rarities Edition- Live", while "?" still becomes "-" and "/" is simply removed. A character left alone follows the general stand-in, shown greyed in its box, and the table stays folded away until you open it. Like the general stand-in, it applies to future downloads only: folders and files already in your library keep the names they have, including the ones named with the general stand-in before an override was added ([issue #16](https://github.com/iamprivacy/Waves/issues/16)).
- 🎁 A recommended set of stand-ins comes with the table: ":" becomes " · ", "/" and "\" become "-", '"' becomes "'", and "?" becomes a full-width "？" so an album called "?" still has a name. A new install starts with them. An existing library is asked first, on the File organization card, because its folders are already spelled the old way: take them, keep removing the characters as before, or set your own. Whichever you pick is remembered, and the card's "Recommended" link brings the set back at any time.

### 🔧 Changed

- 📖 Artist pages open as an overview, the way search results do: every section (Top tracks, Albums, EPs & singles, Videos) shows its first five with a SHOW ALL beneath it, instead of Albums and EPs unrolling in full. A section you expand stays expanded on every artist page you open after it, across launches too, section by section, and showing all of one never touches the others.
- 🖼️ Cover art picks up a soft shadow as it tilts under the pointer, so it reads as lifting off the page rather than only leaning.
- 💿 Album art that is previewing stands out from the artwork around it: it stops tilting toward the pointer, settles very slightly raised, and its shadow deepens and moves underneath it, so you can tell at a glance which cover on a page the music is coming from. Pausing keeps the cover raised and sets the shadow breathing gently instead, and the whole effect eases away as soon as the preview stops. Only the artwork you started the preview on is marked. Turning "Cover art tilts on hover" off in Settings, Advanced, turns all of this off with it.
- 🎰 The hover controls on artwork change over instead of blinking. Pressing PREVIEW or DOWNLOAD used to make the PREVIEW | DOWNLOAD strip vanish and the playback bar (or the download progress bar) simply be there in its place. Now it is one pill throughout: the old contents roll up and out of the top as the new ones arrive from the bottom, and the pill stretches to fit whatever has arrived. It runs the same way in reverse when playback stops or a download finishes. Turning "Hover controls slide in" off in Settings, Advanced, keeps the swap instant, as it does the rest of the hover-control motion.

### 🐛 Fixed

- ⏳ A download button no longer flashes a progress bar before it settles on QUEUED. Some clicks have a step to take first (fetching the item again, loading your playlist folders, comparing an album's editions), and the button acknowledged those by showing progress for a download that had not started, then snapped to the queued design a moment later. It now shows the queued design straight away, and the ✕ that calls the download off fades in once there is a queue entry to cancel.
- 👯 An album carrying several tracks with the same name (Eddie Amador's "I Feel You" and its mixes) downloads all of them intact. When two of them were downloading at the same time they could both aim at the same file name, and one would overwrite the other or quietly fail, which is why a track or two went missing and reappeared as a numbered copy the next time the album was downloaded. A name is now held from the moment it is chosen, and a download that cannot reach its place says so ([issue #15](https://github.com/iamprivacy/Waves/issues/15)).
- 🔤 Two tracks whose names differ only in capitalization, or only in how an accent is spelled internally, keep their own files. macOS and Windows treat those as one and the same file; Waves compared the names letter by letter, so both downloads aimed at what was really one file and one was written over the other. Re-downloading such an album also recognizes its numbered copies now, instead of fetching them again. Nothing is renamed: only the comparison changed, never what is written.
- 🈵 A track with a long title in Japanese, Chinese, Cyrillic or emoji no longer fails to land. The two places that shorten a name to fit counted characters, but the 255 limit filesystems enforce is in bytes, and those scripts cost three or four bytes per character, so the move failed after the download had finished and failed the same way on every retry.
- 📏 A very long track name in a deeply nested folder no longer produces a path the operating system refuses, which on Windows (where the limit is 260 characters) failed at the very last step with "no such file or directory", every retry included, so exactly the longest-named tracks of an album were the ones reported failed. The folder was measured on its own and the file name on its own, and both could fit while the two together did not; and the hidden temporary name each file is moved into place through, a little longer than the final one, was never measured against that limit at all. Names are now shortened just enough to fit, keeping the file extension, and the temporary name gives up its readable part first, so the album folder and the file's actual name are left as they were ([issue #17](https://github.com/iamprivacy/Waves/issues/17)).
- 📝 A lyrics file you edited by hand is no longer replaced by a re-download. The .lrc (or .txt) beside a track followed no rule of its own and was always written over; it now follows the same one the audio does, so with "skip existing files" on it is left exactly as you have it, and with the setting off it is replaced along with everything else.
- 🎶 The generated playlist file (.m3u) is written whole or not at all. It was the one file written straight at its place in your library rather than moved into it, so a crash, a full disk or a network share going away mid-write could leave an emptied or half-written playlist where a complete one had been. The previous one now survives such a failure untouched.
- 📦 The album cover and lyrics files are written into your library only after the track itself has landed there. They used to go first, so a track whose move then failed left a cover.jpg and a .lrc behind for music that never arrived, and Waves never removes a file once it is in your library.
- 📁 A TIDAL playlist folder's name follows the illegal-character stand-ins too, so a folder called "?" keeps its level in your library instead of losing it, and a folder called "Chill: Night" is written with the stand-in you chose for ":" ([issue #16](https://github.com/iamprivacy/Waves/issues/16)).
- 🏷️ A playlist's file name follows the illegal-character stand-ins like every other name in your library. A playlist called "?" used to lose its name entirely while an album called "?" kept one, and a stand-in such as " · " for ":" was ignored there ([issue #16](https://github.com/iamprivacy/Waves/issues/16)).
- 🕳️ An empty file left behind by an interrupted download no longer keeps that track out of your library. A crash or a dropped network share between creating a file and writing it leaves a 0-byte file: the download started again, as it should, but the move then read the empty file as somebody else's and refused to land, every time, for good. The finished download now completes the interrupted write. A file that holds anything at all is still never touched.
- 💯 A track whose name and all 99 of its numbered copies are taken now fails as a download failure and says so, instead of quietly aiming at an occupied name and reporting a collision that was not one.
- ♻️ Two same-name tracks keep their own files when the download is allowed to replace what is already there, which happens when "skip existing files" is off and whenever a track is re-fetched at a higher quality. In those modes no name was held, so the two mixes aimed at one file: one was written over the other, and its old copy was left behind at the lower quality under its numbered name. A name a download is holding is now off limits in every mode, while a file already in your library is still replaced when you asked for that.
- 🔗 With "symlink to track" on, a playlist track whose name collides with a different track already in your artist folder is kept instead of being thrown away. The move into the artist folder recognized a neighbour by its name alone, so it treated a stranger's file as this track: the audio just downloaded was removed and the playlist entry was pointed at the wrong song. Names are now matched by the track they belong to, a genuinely new track lands beside the neighbour as a numbered copy, and two such tracks downloading at once each keep their own file.
- 🔢 Re-downloading such an album recognizes the numbered copies it already made, even where one of them was since deleted or where the copies predate the release that started marking tracks with their TIDAL id. Both cases used to fetch a track again and leave a duplicate behind ([issue #15](https://github.com/iamprivacy/Waves/issues/15)).
- ❓ An album whose title is nothing but characters a filesystem rejects (XXXTENTACION's "?") now gets its own folder when an illegal-character stand-in is set, instead of spilling its tracks loose into the artist folder ([issue #16](https://github.com/iamprivacy/Waves/issues/16)). Such a title leaves nothing behind once the illegal characters are removed, and the guard that keeps an existing library from being restructured mistook the artist folder above it for the album's old home. Tracks already downloaded loose stay exactly where they are, nothing is moved or downloaded twice.

## 🗂️ v0.1.17 (2026-08-09)

### ✨ Added

- 🔣 A new "Illegal-character stand-in" setting (Settings, File organization) chooses what is written where a character a filesystem rejects is removed from a file or folder name: set it to "-" and "AC/DC" is saved as "AC-DC" instead of "ACDC". Left empty, the default, names are written exactly as before. The setting applies to future downloads only, anything already in your library keeps the name and place it has, and the path previews on the Settings page show its effect before anything downloads. Typing a character a file name cannot hold turns the box red and holds Save Changes until it is taken out, so the setting can never be saved as something the download would quietly ignore.

### 🔧 Changed

- 🧹 The File organization settings are laid out more compactly: the short-value boxes (the illegal-character stand-in and the two artist separators) are sized for the character or two they hold and share a single row, the switches beneath them line up three across, and the decorative filler tile that padded out an odd row is gone.

### 🐛 Fixed

- 🌊 The WAVES wordmark no longer hitches partway through its zoom at launch. The interface was being drawn for the very first time in the middle of that animation, which cost one long frame in the same place every time; it is now prepared while the version readout drains, before the zoom starts.

- ↩️ Cancel on the Settings page no longer throws you back to Browse. It now does what it says: puts every setting back the way it is saved and leaves you exactly where you were, at the same spot on the page.

- ✂️ Removing an illegal character from a name no longer leaves its surrounding spaces behind: "The Better Life / Dead Love" now becomes "The Better Life Dead Love" instead of keeping a doubled space ([issue #15](https://github.com/iamprivacy/Waves/issues/15)). Libraries that already hold the old doubled-space names are left untouched: existing folders and files keep receiving downloads under the name they have, so nothing is moved, renamed, or downloaded again.

## 🗂️ v0.1.16 (2026-08-08)

### ✨ Added

- 🚪 Closing Waves while downloads are queued or running now asks first: exiting ends them and unfinished tracks would need downloading again, so a small prompt offers Keep Downloading or Exit Anyway, with a "don't warn me again" checkbox for people who prefer the old instant exit. With an idle queue, closing exits immediately as before.

- 🔔 Waves now asks whether it may check for updates. Update checks have always been off by default and nothing ever raised the question, so people stayed on old versions while the problems those versions had were already fixed. New installs meet the question at the end of first-run setup, right after the privacy terms; existing installs meet it on the first launch after updating. Saying yes turns on the once-a-day check and runs one right away; saying no is remembered and the question never returns. Nothing is ever downloaded or installed without you choosing it, and the check can be turned on or off in Settings as before.

- 🎬 An artist's videos can now be downloaded in one click: the VIDEOS section on the artist page carries its own All Videos button, with the same live progress and retry states as the discography button. It queues only the videos, and it works whether or not "Music videos" is enabled as a discography source (that setting only governs what a full discography download includes).
- 🏷️ Downloaded music videos now carry real metadata: title, artists, release date, explicit rating, an embedded thumbnail, and the media-kind tag that makes players and library managers file them as music videos. Applies to videos converted to MP4 (the default).
- 📋 Playlist search results now behave like albums: click the row to expand the track list in place (with per-track selection and Download selected), and click the title to open the playlist's page. Every search starts its playlist rows fresh, a track the playlist contains twice gets its own checkbox per appearance, and ticks made while the list refreshes survive the refresh.
- 💿 While a track buffers, the cover art now spins up like a record, accelerating through the first turn then cruising. The moment the song is ready the disc eases back to rest, a spark pops in at 12 o'clock, and the progress ring rises from that exact spot.

### 🔧 Changed

- ⚡ Previews start much faster. The audio pieces of a track are now fetched all at once instead of one after another, so the wait is set by your connection speed rather than by the round trip to the server repeated dozens of times; when that fast path hits a network error it hands over to the slower fallback immediately instead of waiting out every remaining piece. The track title, artist and artwork also appear the moment you press play instead of only when the audio is ready, so there is no longer a blank bar during the load.
- 📺 The Video download switch moved from Downloads to Discography & editions, renamed "Music videos", and now actually does something: with it on, Download discography also downloads all of the artist's music videos alongside the albums. Downloading a single video yourself always works, with or without it. Because the old switch was connected to nothing, it starts off for everyone after this update: flip it on in Settings to opt in.
- 🐍 The buffering snake hunts faster on video loads, with its food spawned just ahead of it, and the word BUFFERING returned to the bottom bar while a preview loads, shown only when there is room for it.
- 🎚️ The "Concurrent downloads" setting is now "Concurrent track downloads": it always governed the parallelism inside one album or playlist, and with the queue now strictly ordered that is its only meaning.
- 📁 Videos now save into a folder per artist with the release year leading the file name (Videos/Artist/[2026] Song), so a plain file explorer sorts them chronologically. The folder uses only the primary artist, so collabs land in that artist's folder instead of minting a new "Artist1, Artist2" folder per combination; every credited artist is still written to the file's metadata. A customized video template is never touched; only the old default follows along.

### 🐛 Fixed

- 🔇 Turning Dolby Atmos downloads off now really skips Atmos tracks ([issue #15](https://github.com/iamprivacy/Waves/issues/15)). An album's separate Atmos edition has no non-Atmos stream to fall back to, and it used to be downloaded anyway; with the option off it is now skipped with a log line instead.
- 👯 Tracks that share a filename are no longer lost ([issue #15](https://github.com/iamprivacy/Waves/issues/15)). Distinct tracks whose names collide (several mixes with one title) used to be skipped after the first one, or overwrite each other. Every download now carries its TIDAL id in a tag, colliding tracks get numbered names, and "skip existing" compares ids instead of trusting the filename, so re-downloading an album completes the set without duplicating it. Untagged files from older releases keep the old skip behavior.
- 🍎 Waves runs on macOS 12 Monterey and newer again, and says so ([issue #14](https://github.com/iamprivacy/Waves/issues/14)). The Qt release the app bundled claims to support macOS 13 but secretly ships libraries built only for macOS 15, so on anything older the app just bounced in the Dock and died with no message. Each release now ships two macOS downloads: the regular build (current Qt, macOS 15 and newer) and a legacy build marked `legacy` in the file name (the newest Qt honestly built for macOS 12, for Monterey through Sonoma). The in-app updater and Homebrew both pick the right one for your machine on their own, each app declares its minimum so an unsupported system gets a clear "requires macOS N" dialog instead of a silent bounce, and the release pipeline now verifies the real floor of every file in both bundles on every build.
- 🔢 The download queue now processes items strictly in the order you queued them, one at a time. Several items used to run side by side, which read as the queue jumping around: a long album crawled while single tracks queued after it finished first, because everything running at once was competing for the same connections. Parallel downloading still happens inside each album or playlist (the "Concurrent track downloads" setting), so this costs little speed and the queue keeps its promise of order.
- 🔎 A new search now always starts at the top of the results page. It used to keep the previous search's scroll position, so the fresh results could open already scrolled deep into the albums or tracks. Returning to Search from another tab still restores your exact spot.
- 👀 The download queue's hover peek (and an expanded album's track list) no longer jitters while that album is downloading; live progress ticks were rebuilding the visible track rows twice a second.
- 📅 On video results, a long artist credit line no longer pushes the release date underneath the download button; the date keeps its place and the artist list trims to the room that is left.
- 🌊 Switching to another app, or back, no longer disturbs the moving water, the pulsing download dots or any other ambient animation. Two separate faults were at work: the animations paused the instant the window lost focus and lurched back to life on return, and the switch itself froze the whole window for a third of a second in each direction. Animations now keep flowing while the window is visible and pause only when it is hidden or minimised, a window that was covered or minimised wakes on the exact frame it went away on instead of jumping ahead, and clicking the search box while Waves is in the background selects the whole term again so the next keystroke replaces it.
- ⏱️ Moving the mouse across a playing disc to pause it no longer flashes the seek-time readout in passing. The readout and its ring marker now appear once the pointer rests over the ring for a moment, both fade in and out gently, and the play/pause glyph over the art eases in and out instead of popping.
- ⏯️ When a preview finishes buffering, the mini player in the bottom bar no longer shifts sideways as the buffering label collapses; the label now simply gives way to the play glyph and the title, artist and artwork stay put.
- 🔄 An artist's download button no longer spins forever when one member of the discography could not be re-fetched for its download; the failure is now counted and the button settles, showing that something failed instead of running without end.
- 🐢 Progress bookkeeping no longer does work proportional to the queue's length on every tick, so the window stays responsive while a very large discography (hundreds of albums and videos) is queued.
- 🙈 The optional "also hide titles and searches" switch on diagnostic exports now also covers the download engine's log lines that name a track, album, artist or video; a handful of engine messages used to escape the hashing.

## 🗂️ v0.1.15 (2026-08-05)

### ✨ Added

- 🎤 Lyrics now come from the community LRCLIB database first (the same source the LRCGet app uses), with TIDAL's own lyrics as the fallback. TIDAL machine-transcribes lyrics for many newer track IDs (re-recordings and reissues especially) and often gets them badly wrong; LRCLIB carries human-submitted synced lyrics. A new "Prefer LRCLIB lyrics" switch in Settings turns this off.
- 📝 Saved lyrics files now match their content: timed lyrics keep the .lrc extension, untimed lyrics are written as .txt instead of a fake .lrc. A nested "Only when lyrics are timed" option under Save lyrics file skips the .txt entirely if you want lyrics files only when they are timed.
- 📺 A "Videos preview on hover" switch in Settings > Advanced: turn it off and video thumbnails stay still, with no sound-on preview growing from a hover; videos then play only when clicked.

### 🔧 Changed

- 🖼️ Video search results are art-first: each video is now a large 16:9 thumbnail in a grid that follows the window width, with its title, artist, release date, resolution and a full Download button underneath instead of a thin one-line row. Hovering still previews the video, and clicking still opens the player.
- 🖱️ Hovering across video results no longer previews every thumbnail the pointer crosses: a preview waits for the pointer to actually rest on a video, and a brief pause follows one closing.
- 🌀 The mouse wheel scrolls the results again while a video preview is open, instead of being swallowed by the preview card.
- 🎬 Artist pages now end with a VIDEOS section: the artist's music videos in the same art-first grid as the search results (hover preview and all), whole rows only with SHOW ALL, collapsible like the other sections.
- 🏅 A video's resolution now sits as a small gold badge on the thumbnail's top-right corner instead of a VIDEO tag beside the title, which ate most of the title on typical song names.
- 👯 Duplicate video listings (the same video re-listed per quality, region or clean/explicit edit) now collapse to one, following the explicit preference, exactly as albums and tracks already did. Same-titled but genuinely different videos are kept.
- ↕️ The search sort control (Relevance, Release date, Name and the direction arrow) now reorders the tracks and videos sections too, instead of quietly applying to albums only.
- 🐍 A video preview that is still loading now waits on a "LOADING VIDEO" panel with a small snake circling it, eating the bites laid on its path and growing as it laps (the first bite always lands right ahead of it, so even a quick load shows a catch), instead of blowing the thumbnail up to a size it was never made for.
- 🔗 The lyrics settings are linked instead of free-floating: the synced-only option lives inside the Save lyrics file tile, and Prefer LRCLIB lyrics greys out while both lyrics switches are off.
- 🗂️ Settings sections all start collapsed on a first visit (Downloads is no longer forced open) and remember which ones you open or close, across visits and across launches. Links that jump to a specific section still open it for you.

### 🐛 Fixed

- 👻 The opening wave animation no longer hides a live, clickable interface: a click on the launch screen used to land on the invisible page behind it and could start a full-volume preview out of nowhere, which read as Waves autoplaying at startup ([issue #13](https://github.com/iamprivacy/Waves/issues/13)). The launch screen now swallows clicks and scrolling, the interface stays inert until it is actually visible, and the cursor no longer turns into a pointing hand over buttons that cannot be seen.
- ⏳ The launch animation holds its opening frame until your home landing has actually arrived, so a slower sign-in no longer ends the reveal on an empty page that loads in afterwards. A dead network still can't pin the launch screen.
- 🔌 A download folder on a network share that macOS quietly ejected (sleep, a network blip) now gets mounted back automatically, the same request Finder makes when you navigate to the share by hand. Waves remembers where the share came from while it is healthy and asks macOS to reconnect it (using the credentials saved in your keychain) whenever a download, "Try again" or the background recovery watch finds the volume gone, instead of endlessly re-checking a mount point that could never come back on its own.
- 🧟 A network mount that is still listed but answers nothing (the zombie state a hung SMB session leaves behind) is now force-ejected and mounted back after a few seconds of silence, instead of being watched forever.
- 🛟 A download that loses its folder mid-flight (share ejected or hung between the start check and the writes) is now held and retried automatically like any other folder outage, instead of turning the button red with a failure no dialog ever explained.
- 📂 The Browse button in Settings opens at the saved folder's nearest existing ancestor when the folder itself is unavailable, instead of wherever the picker last was.
- 📍 Returning to Settings now puts you back at the exact spot you left, even right after saving a change (the save used to rebuild the page on the way back in and strand the view near the top).
- 🏷️ Tracks with only untimed lyrics now show them in players that read just the main lyrics tag (the FLAC and M4A primary field used to stay empty unless timed lyrics existed). Re-downloading a track that later gains timed lyrics upgrades the tag to them.

## 🗂️ v0.1.14 (2026-08-03)

### ✨ Added

- ⭕ Track previews are seekable: hover the progress ring around the album art for a ghost marker and time readout, then click or drag to seek. The ring is now a smooth arc instead of blocky cells.
- 🎚️ The playback line along the bottom of the window is a seek bar: hover for a time readout, click or drag to jump. Slightly taller, with a glowing spark at the playhead.
- 🎴 Album covers tilt toward the cursor and lift while hovered, springing back on leave. The tilt waits for the pointer to rest, so lists scrolling under a still cursor stay calm. Round track thumbnails and the Browse front page's big cards join in. Settings > Advanced turns it off for anyone who would rather artwork stayed flat and still.
- 👀 Hover video peek: rest on a video thumbnail and a floating preview grows out of it, playing with sound but no controls, so you can tell what a video is before opening it. Clicking it opens the full player without a break (the preview simply becomes the player, then steps up to your usual quality). Move away and it goes away.
- 🔖 The status bar's wordmark now ends with the version you are running, and clicking it checks for an update on the spot: it reports back "up to date", or raises the usual update notice when a newer build is waiting.

### 🔧 Changed

- ▶️ The full-width green PLAY bar across video thumbnails, which covered a quarter of the picture, is now a small play mark in the corner over a soft shadow.
- 🔌 When the download folder is on a network drive that dropped off, Waves now keeps checking for it and resumes the held downloads on its own the moment the drive is back, instead of waiting for you to click Browse or "Try again". On a Mac, a remounted volume is noticed immediately.
- 😴 A network folder that is mounted but slow to wake (macOS quietly drops idle connections and reconnects on first touch) no longer triggers the "isn't reachable" dialog: the download waits out the wake-up and starts by itself. While Waves runs, it also keeps the connection warm so the folder rarely goes to sleep in the first place.
- 💡 The Browse button next to path settings stays at full strength once a path is set, so it always looks as clickable as it is.

### 🐛 Fixed

- 🔒 Waves no longer crashes at launch on Windows when another program (an antivirus scan, a backup tool, or a second Waves instance) briefly holds the settings file: the save retries for a moment and, at startup, falls back to in-memory settings instead of failing to open.
- 💾 Finished downloads are flushed to stable storage before they get their final name, so a power cut mid-move can no longer leave a truncated file under a name Waves would trust. Zero-byte leftovers from an older crash now read as "not downloaded" instead of being skipped forever.
- 🛟 An update whose unpacked payload is missing the app executable now restores the previous install instead of leaving the app uninstalled.
- 🔢 The path preview for video templates now pads numeric tokens exactly the way real video downloads do.
- 🚪 Signing out now fully forgets the previous account: cached items can no longer be fetched or downloaded through the old session, and the old account's page snapshot can no longer be re-written to disk mid-sign-out.
- 🧠 Several long-session memory and freshness gaps are closed: drilled Browse pages and the ownership cache are now bounded, tile-art mosaics refresh weekly even when the app never restarts, and cache eviction can no longer race between loaders and drop a page repaint.
- ↕️ Re-sorting a My Tidal category while it is still loading more rows no longer splices the old order into the new list or skips a window of items.
- 📁 Opening Mixes first no longer makes Playlists briefly render (and save) a folder-less list.
- 🩹 The download-folder auto-heal is stricter and fairer: it only follows a remounted volume that actually carries your library folder (never a different drive that merely shares the name), a healed folder now reaches the download already in progress, changing the folder mid-download no longer skips the new folder's reachability check, and a share with odd delete semantics no longer reads as dead.
- 📥 The download queue can no longer lose a just-added row when another download finishes at the same moment, and an album finishing quickly no longer skips recording which tracks belong to it.
- 🗑️ A failed preview no longer leaves an orphaned temp file behind, and quitting mid-FFmpeg-install no longer orphans a partial archive.
- 🗄️ Quitting no longer risks "database is closed" errors from ownership records still being written.
- ❌ Cancelling an app update now also works during the install phase, and a failed update no longer leaves a full extracted app copy in the config folder.
- 🧩 Path templates: {album_date} and {isrc} without a value no longer write literal braces into folder names, {album_artist} on a release with no artist credit no longer fails the download, and de-duplicated filenames at deep paths are no longer shredded to "\_01.flac".
- 🌀 A hiccup loading Mixes no longer makes My Tidal report "0 playlists".
- 🔁 RETRY works on every Browse page again: drilled playlist grids and playlist/mix/album pages used to clear the error, show "loading" and never load; the button now re-requests the right page.
- ⏳ An expanded album no longer sits on "Loading tracks…" forever after a search; the track list is re-fetched and a failure leaves the row re-expandable instead of dead.
- 🎯 Fast scrolling through long album lists can no longer carry one album's selected tracks over to another row and download the wrong tracks.
- ⚙️ Clicking Install twice (in Settings and on the update toast) no longer runs two updaters over the same staging folder, and cancelling an update can no longer be undone by a stray second Install click.
- ♻️ RETRY on a failed download row works even after searching for something else in between; a row whose item cannot be re-fetched keeps its RETRY instead of dying silently.
- 🐧 On Linux, an update started from a build that was launched out of another application's AppImage environment no longer overwrites that application's file.
- 🔄 The Settings page now refreshes when the app changes a setting on its own ("Don't ask again", the player's video quality menu, the download folder auto-heal), instead of showing the old value until a manual save.
- 📌 Picking a network share (\\nas\music) as the download folder now saves the real share path; it used to be saved as a relative folder that landed downloads next to the app's working directory.
- 🧱 Drilled Browse pages with link tiles (e.g. Record Labels) keep their cover mosaics on revisits and from the second launch onward.
- ⏲️ DOWNLOAD ALL on a Browse playlist category now waits for your playlist folders to load first, so foldered playlists are not written a second time outside their folder.
- 💿 Clicking an album's download button again while its edition scan is still running no longer queues the album twice into two folders, and a failed scan returns the button instead of leaving it stuck.
- 🧭 Going Back to an artist page that fails to load (e.g. offline) no longer stops the app from recording navigation history.
- 📏 Tracks with very long names now download reliably to network (NAS) and external libraries: the temporary staging file no longer overflows the filesystem's name-length limit.
- 🔗 With "symlink to track directory" enabled, downloading a playlist whose tracks are already in the track library no longer crashes the playlist job; the playlist folder is created for the symlinks, and a failed symlink is logged instead of failing the download.
- 📶 A brief network blip while sizing a track no longer fails the download: the progress bar just runs without a percentage. Redirected download links no longer show a stuck 0% bar.
- 🏠 A track whose full path exceeds the operating system's limit (mostly a Windows concern) now shortens its folder names to fit instead of silently saving into the user's home folder, outside the library.
- 🧨 "Reset application" now also erases previously exported diagnostic bundles, matching what the confirmation dialog promises.
- 🚦 Saving the page cache while the app is busy loading in the background can no longer crash a loader and leave the busy indicator stuck on.
- 🎛️ Bulk downloads (a folder, a Browse category, a whole discography) started without FFmpeg set up now ask about FFmpeg first and continue after "Continue anyway", instead of freezing the button at "running" for the whole session.
- 🤝 Downloading two discographies that share an album (or a guest track) now completes both artist buttons; before, the second one could stay stuck at "running" forever.
- 🔘 A download button no longer dies for the session when an aged-out item is re-fetched while no download folder is set, or when the download-folder question is dismissed: the button returns to idle so it can be clicked again.
- 🛡️ An artist page whose fetch partly failed (for example a rate limit on the albums list) is no longer saved or allowed to wipe the album grid on screen; the last good page stays until a complete fetch succeeds.
- 🗃️ A failed album track-list fetch no longer erases what Waves had already learned about that album's tracks (ownership badges kept working data).
- 🔂 A failed playlist-folder scan no longer retries in a loop with the download button spinning forever; it stops, says so, and your next click retries.
- ✂️ The favourites list used by library-scoped artist pages is no longer silently truncated when TIDAL returns a short page, and a failed load is no longer remembered as "no favourites" for ten minutes.
- 🗂️ A library tab whose very first load failed no longer saves an empty page as the truth; reopening the tab retries instead of showing an empty library.
- 🚫 Downloading a discography now refuses to run on a partial scan (some release lists failed to load) instead of quietly downloading a truncated discography and reporting success, or claiming "No albums to download".
- 🎼 A "best of both" album merge no longer skips tracks you already own in a different folder: every track lands in the merged album's own folder, so the album is complete on disk when the download reports done.
- 🛑 If one edition's track list cannot be fetched during a best-of-both scan (a network hiccup, a region-locked edition), that edition is kept and the merge is called off instead of silently dropping its exclusive tracks.
- ✅ A merged album now remembers it was downloaded: its download button stays on DOWNLOADED after you reopen the album or restart the app, instead of flipping back as if nothing had been fetched.
- 📊 The queue drawer shows a merged album at its real length, with every row progressing, instead of doubled-up rows half stuck at "pending".
- 🩺 Exporting a diagnostic report no longer switches your two diagnostics settings back off. Turning on verbose diagnostics and "also hide titles and searches", reproducing a problem, then clicking Export used to silently disable both and produce a report containing the very details you had asked to hide. Settings also now shows the true state of those switches when you reopen the page.
- 🧊 Previewing or downloading an artist from a card that came out of the search cache no longer freezes the window while it fetches.
- 🚧 A stalled connection to TIDAL while Browse is loading no longer blocks search, artist pages and downloads along with it.
- ❓ An artist whose name is only punctuation (`?`, `*`, `<>`) no longer sends the download outside your download folder. On Windows those tracks were written to the root of the drive and still reported as done; on macOS and Linux the download failed with an unexplained error.
- 🕶️ Dialogs now dim the window behind them. Every modal (download folder, bulk-download confirm, settings reset, factory reset, folder unreachable, FFmpeg, terms) and the sign-in panel were drawing their backdrop at 2% opacity, so the interface behind stayed fully bright. The video overlay, the play/pause glyph disc and the "buffering" text outline had the same fault.
- 🔏 Answering the download-folder question with "keep it", or ticking "Don't ask again" on the bulk-download confirm, no longer silently switches off FLAC extraction and video conversion on disk (the loss only showed up on the next launch), and no longer writes a path containing your username into `settings.json`.
- 🧽 `crash.log` is now scrubbed the same way every other log is. Crashes could previously write your username, home folder path and the name of the track being handled into that file, which the bug-report template asks you to paste into a public issue.
- 🙈 "Also hide titles and searches" now actually hides them. The switch had nothing to act on, so an exported report taken with it turned on still contained your search terms and media names.
- ➿ An album or playlist containing one track that fails to download no longer loops forever, re-downloading and rewriting every other track in it on every pass. The download now finishes and the queue row completes, instead of staying stuck on "running" until cancelled.
- 👻 Downloading from a Mac to a WebDAV network drive no longer litters the server with hidden 4 KB `._` companion files next to every track, cover and playlist (macOS metadata files that other systems show as ghost files). Generated m3u playlists also no longer pick up any existing `._` files as tracks.
- 🟢 Album rows in search results now show the artist name, clickable in green like everywhere else, both on the collapsed row and in the expanded album panel.
- 📜 Playlists longer than 200 tracks now show every track: the track list stopped after two pages of the paged playlist endpoint, truncating long playlists even though the header count was right ([issue #12](https://github.com/iamprivacy/Waves/issues/12)).
- 🚀 Very long playlists scroll smoothly again. Waves now builds only the rows around what you are looking at and fills the rest in as you scroll, instead of building all of them at once, so a several-hundred-track playlist no longer stays sluggish the whole time you are on it. Download buttons also stopped building their progress bar while nothing is downloading. Together these cut the memory a browsing session holds by roughly eight times.
- 🔙 Browse and My Tidal keep their pages alive behind the scenes: going Back or Forward between the Browse front page and an open playlist or album is instant and lands exactly where you left off, with nothing visibly assembling, scrolling or reloading, and each My Tidal category (Albums, Tracks, Artists, Playlists, Mixes, Videos) holds its rows, scroll position and expanded albums for as long as the app runs instead of reloading every time you switch. Leaving for another tab and returning is just as seamless, and fresh favourites are still picked up quietly in the background without moving the page under you.
- 🍞 A page reached by returning to its section through the nav tabs no longer leaves you without breadcrumbs: the trail now always begins with the section's home pill (Browse, Search, or My Tidal), so there is always a visible way back up, not just the Back gesture.
- 🥖 Breadcrumbs no longer wipe in whenever a page opens: crumbs now appear in place, with only a barely-there fade when one is removed.
- 🌊 The launch animation hands over cleanly again: the version readout always finishes its drain before the wordmark zooms away and the interface fades up, instead of the two overlapping on a busy start.
- 📍 The Settings page holds your place: changing a setting, or leaving for another tab and coming back, no longer collapses the sections you had opened or throws you back to the top of the page.

## 🗂️ v0.1.13 (2026-07-28)

### 🐛 Fixed

- 🌊 The opening WAVES animation is clean water again: the dark scroll-edge fades of the page loading behind it no longer show through as bands across the top and bottom of the launch screen.

## 🗂️ v0.1.12 (2026-07-28)

### ✨ Added

- 🖱️ The mouse "forward" side button now navigates forward, completing [issue #8](https://github.com/iamprivacy/Waves/issues/8) alongside the back button shipped in v0.1.11.
- 📁 Your TIDAL playlist folders now appear in My Tidal > Playlists ([issue #11](https://github.com/iamprivacy/Waves/issues/11)). Folders drill in like a file manager with a breadcrumb strip, and each folder row has a "Download all" button with a count badge that ticks down like an odometer as each playlist finishes, ending on a checkmark. On disk, playlists mirror your TIDAL folder tree (for example Playlists/Country/Bluegrass/My Playlist/), even when downloaded one at a time, via a new {folder_path} piece in the playlist path template. A playlist that is in no folder lands exactly where it always did, and a customized "Playlist path & name" setting is left untouched: add {folder_path} wherever you want the mirroring. Files already on disk are not moved, so a foldered playlist you download again lands in its new nested spot beside the old flat copy; drop {folder_path} from the setting to keep the previous layout.
- 📄 Playlist rows in My Tidal now open the playlist page (the same art header and track list playlists get in Browse), inside folders too.
- 🗂️ Browse gains a folder-style "All Playlists" section: drill from playlist folders (moods, genres, decades) into a grid of just that category's playlists, no albums or tracks in the way. Hovering a folder offers PREVIEW and DOWNLOAD ALL: the whole category queues in one click, with the count badge ticking down as playlists finish. A confirm states how many playlists are about to download ("Don't ask again" available). Both landing styles get there: in the console style the section headlines are now openable too, so the genre and decade folders are reachable without switching to the art view.
- ↩️ Every path, name template and separator in Settings now carries its own default. A field you have changed offers "Restore default" beside its label and goes back to the shipped value on its own, with nothing else reset; an unchanged field just reads "Default", so you can see at a glance which settings you have edited.
- 🧿 Browse now carries the personalized shelves from TIDAL's home page: "Essentials to explore", "Popular playlists on TIDAL", "Albums you'll enjoy", "Suggested new albums for you", "Your forgotten favorites", your genre rows and more, each one downloadable like any other Browse row.
- 🍞 The "Back to X" bars on artist and Browse pages are now a breadcrumb trail: every step of how you got there as clickable pills, with the page you are on lit at the end. Click any crumb to jump straight back to that spot (scroll position and expanded albums included). Long trails fold their middle behind a "…" pill that expands in place and turns into "›‹" to fold back down. The trail shows where you drilled in, not the tabs you passed through: switching to Browse, Search or My Tidal starts it fresh rather than piling up Search > My Tidal > Search. Back and Forward still cross between tabs as they always did.

### 🔧 Changed

- ✨ The preview and download controls on covers no longer fade in on hover: they rise from the bottom edge with a soft bounce, and drop back out of sight when the pointer leaves. Settings > Advanced > "Hover controls slide in" turns the movement off and brings the plain fade back.
- 🧹 The two artist separator boxes now share one row instead of taking a full-width box each for a single character.
- 🔲 The download and preview buttons carry a slightly heavier outline, and so does the divider between PREVIEW and DOWNLOAD, so they hold their edge over busy artwork.
- 💡 Hovering a download control now lights it up: the outline of the part under the pointer (just that half, on the PREVIEW | DOWNLOAD pills) turns bright and breathes slowly, and fades back out in place when you leave.
- 🌊 The launch screen opens more gently: the WAVES wordmark is larger, the version sits tucked under it instead of adrift below, and both fade up together rather than appearing all at once. The hand-over to the app now reads as two clean beats as well: the version empties out completely, and the wordmark starts zooming only once it has, instead of the tail of one running under the start of the other.
- 🎞️ Rows that scroll sideways (the Browse and My Tidal shelves, the folder tiles, the artist strip in search) now fade at their left and right edges, the same soft edge the top and bottom of a page already had, so covers slide out of frame instead of being cut off. The fade only appears on the side you have actually scrolled past: a row that fits on screen keeps both edges clean.

### 🐛 Fixed

- 👯 Browse no longer shows the same covers twice. TIDAL serves one set of releases under several headlines ("New Albums", "New releases for you" and "Suggested new albums for you" were the same albums three times), so a shelf whose items all appear in a larger one is now dropped and only the row carrying the most of them is shown. Shelves that merely share a few entries are untouched.
- 💬 Settings descriptions read properly again. Every comma in them was being shown as a semicolon ("16 Bit, 44,1 kHz" appeared as "16 Bit; 44,1 kHz"), and the artist separator fields advertised a default of "; " when they actually ship with ", ".
- 🧭 Opening an album or playlist you had visited earlier in the session now remembers where you came from. Before, Back skipped that spot (a playlist folder, a My Tidal shelf) and jumped to somewhere older, often Search.
- 🎯 Clicking an artist or album name while a search (or a pasted TIDAL link) was still resolving no longer yanks you to the search page when its results finally arrive: late results are dropped, your click wins.
- 🌊 The launch sequence stays fluid, and hands over to a finished page. When Browse's landing refreshed behind the opening water (its editorial and For You shelves change between sessions), the rebuild froze the animations for a beat; and the wordmark lifted on its own schedule, so a landing still assembling was watched dropping in shelf by shelf. The refresh now builds in the background, and the launch screen holds until the page behind it is complete.

## 🖱️ v0.1.11 (2026-07-21)

### ✨ Added

- 🖱️ The mouse "back" side button now navigates back, same as the Back bar and the macOS three-finger swipe ([issue #8](https://github.com/iamprivacy/Waves/issues/8)).

### 🐛 Fixed

- ✂️ A download whose final piece fails can no longer be saved short and reported as done. TIDAL delivers tracks in pieces, and on very short tracks the server lists one piece more than the audio actually has, so a failure on the last piece used to be waved through for every track. Waves now reads the track's manifest to tell the harmless extra piece apart from a real final piece, and a genuine failure there marks the download failed instead of leaving a truncated file.
- 🚀 Waves can no longer hang on "Signing in…" at launch. If an internal cache file had been damaged, the sign-in never finished: the startup screen sat there forever and you stayed signed out. An unreadable cache is now discarded and start-up carries on without it.
- 🔑 A network problem at launch no longer signs you out for good. If Waves could not reach TIDAL while restoring your saved sign-in, it deleted the saved credential, so the only way back was a full re-login. It now keeps the credential and simply reports you as signed out; retrying, or restarting once you are back online, restores the session. A real rejected or damaged sign-in is still cleared, as before.
- 💾 Your settings and saved sign-in are now written whole or not at all. A crash or power cut during a save could leave a half-written file, which meant corrupted settings or a lost sign-in on the next launch. Waves now writes to a temporary file and swaps it in, so what is on disk is always either the old version or the new one.
- ⬇️ An album or playlist where some tracks failed no longer reports a clean "Done". A 20-track album that lost one track rode its 19 successes to a green done and dropped the missing one silently. It now shows as failed and says "1 of 20 tracks failed"; retrying re-downloads only the tracks you are missing, so it is cheap.
- 🚨 A download of a partially owned album now reports a failure when none of its new tracks could be fetched (for example on an account without download entitlement). Before, the tracks skipped as already-in-your-library counted as successes, so the job could show a green "done" while every new track had silently failed. An album where everything is already owned still completes as a success.
- 🎚️ Changing the audio quality in Settings now applies to the very next download; previously new downloads kept the old quality until the app was restarted ([issue #9](https://github.com/iamprivacy/Waves/issues/9)).
- ↩️ Going Back from a playlist (or any long page) to Browse now returns you to the spot you left, instead of jumping to the top of the page.

## 🔎 v0.1.10 (2026-07-15)

### ✨ Added

- 🧹 Advanced settings gained two reset actions at the bottom of the section: "Reset all settings" puts every option back to its factory default (you stay signed in), and "Reset application" erases everything Waves has saved on this computer (settings, sign-in, caches, ownership history and logs, never your downloaded music) and closes the app so the next launch starts like a brand-new install. Both ask for confirmation before anything happens.

- 🪟 Waves now remembers its window size, position and maximized state, and restores them the next time you open it. If the monitor it was on is gone or its resolution changed, the window is nudged back onto a visible screen so it can never open off-screen. On a first launch, before anything has been remembered, the window opens centered at a 4:3 size instead of wherever the OS drops it.
- 🖱️ Clicking the blank space of a track row now goes where clicking the track title goes (the track's album page, or the video player), so most of the row is clickable while the artist and album links beneath the title still drill into their own pages.
- 🔎 Clicking or tabbing into the search box selects the whole current term, so you can start typing to replace it (no highlighting or backspacing first).

### 🔧 Changed

- 🌊 While a page loads, the finished page now fades in gently over the ambient water animation rather than snapping on in one hard paint. The "Reading the wire…" hint rides that same living water while it works.
- 🎚️ The mini player in the bottom bar now sits in the right corner while that corner is free, leaving the middle of the bar clear. If an update notice needs the corner, the player slides to the centre in one smooth move and slides back when the corner frees up again.
- ⚡ The wave-logo box now carries an occasional lightning storm at rest: seven strikes spread across a slow 20-second loop, tall bolts framing the box at the left and right, smaller ones scattered between, and a big centre strike that lights the whole box with a brief flash. Hovering the box still summons the full storm.
- 🕶️ The soft darkening at the top and bottom scroll edges now appears only while rows are actually being cut off there. At the top or bottom of a page it fully lifts, so artist artwork, heroes and the back bar are no longer dimmed when the page is not scrolled.
- 🧭 Collapsing an expanded section with SHOW LESS now brings you back to the top of that section (with a little breathing room above), instead of dropping you at whatever the bottom of the shorter page happens to be.
- 🔤 Track titles in track rows (search results, top tracks, album pages, recent tracks) are now slightly larger and a touch heavier than the artist and album line beneath them, so the title leads the row at a glance.
- 📂 The Browse… button next to folder and file settings lights up green while the field is still empty (it is the thing to click) and settles to a faded green once a value is set.
- ✳️ The SHOW ALL links under top tracks, search sections and the artist strip are now a soft mint green at rest, so it is clear at a glance that they can be clicked (they used to sit grey until hovered).
- 🔊 ReplayGain tags are now written by default, so players that support it can level volume across your library without changing the audio. This update switches it on for existing installs too; you can turn it back off any time under Settings > Advanced > Write ReplayGain tags. Tracks TIDAL never measured are left untagged instead of stamped with a wrong level, and gain is written in the standard "-7.36 dB" form.
- 🔎 Every section on the search page now shows just its first few results with a SHOW ALL beneath it, so the page reads as a quick overview instead of a long page you scroll past: albums, tracks, videos, playlists, and mixes each show their first 5, and artists sit in a single sideways-scrolling row. Whichever sections you open are remembered and stay open on your next search, per section, so you do not have to expand them again each time. Picking a single category from the filters still shows everything in it. Results collapsed behind a SHOW ALL do not download their covers until you expand them, so the art you can actually see loads sooner on a new search.

### 🐛 Fixed

- 🖼️ Cover art keeps loading and the app stays smooth to scroll while downloads run: progress updates no longer redraw every download control on the screen dozens of times a second.
- 📁 On macOS, a download folder on a NAS or external drive stays valid between launches instead of reading as unreachable until you re-pick the same folder. macOS grants that access when you pick the folder (usually silently, sometimes with a one-time prompt) and now remembers it.
- 🖱️ The mouse cursor works normally on the search page again: buttons show the pointing hand instead of the plain arrow. The focused search box was quietly overriding the cursor for the whole window.
- ⌨️ Clicking outside any text field now releases it, the blinking cursor and green outline go away, matching how the search box already behaved. Settings fields like the download folder path used to hold their outline until you clicked another field.
- 🏷️ The search category filters (All, Artists, Albums, and so on) stay put instead of fading out and back in on every search, and they appear as soon as results arrive instead of only after the result cards finish drawing.
- 🖼️ Artist artwork no longer flickers to grey boxes while you resize the window; covers hold their image steadily instead of reloading on every frame of the drag.
- 🎞️ Result rows no longer hold a stale look after switching tabs or changing the result filter; the subtle curve at the top and bottom edges now settles into place right away instead of only correcting once you scroll.
- 🪟 Resizing the window on the search page no longer stutters or jumps: the matching and similar artist cards now hold a fixed size, so a resize reveals more or fewer of them instead of rescaling every card on screen as you drag.
- 🖼️ Track rows in search results no longer show an occasional blank grey circle where the album cover should be. The small round covers now load the same reliable way as the rest of the app (with caching and a retry), instead of a one-shot fetch that could silently fail and leave the circle empty until you reopened the album.
- 🧭 Browse stays current while you keep it open. Its New, Top, and For You rows now refresh on a timer as well as when you return to the tab, so an app left running for days follows what TIDAL is featuring instead of staying pinned to whatever loaded when you first opened it.

## 🚀 v0.1.9 (2026-07-14)

### ✨ Added

- ⏳ Download buttons acknowledge the click instantly with an animated QUEUED state, then flip to the usual progress bar when the download starts.

### 🔧 Changed

- 🚀 Finished tracks land on network drives much faster: a few large writes instead of hundreds of tiny ones, with far less folder and bookkeeping chatter per album.
- 🧵 Downloads use fewer threads and less memory.
- 🟩 The playback ring around track art in search results uses the same square LED blocks as every other progress bar and stays inside the artwork tile.

### 🐛 Fixed

- 🧊 No more multi-second freezes while downloading to a network drive (macOS SMB shares especially): finished-track bookkeeping now runs fully in the background.
- 📡 A busy network share no longer trips the "Download folder isn't reachable" dialog over and over: busy is no longer mistaken for dead, and slow shares get more time to answer.
- 🔁 "Try again" on the unreachable-folder dialog retries every queued download, not just the most recent click.
- 📂 A brief network-share hiccup no longer fails a whole album: creating the destination folders now retries with backoff.
- 🔄 Quitting during a background update check no longer records a bogus "background worker crashed" error in the diagnostic log.
- 🖼️ Cover art no longer stalls on its loading placeholder or fails in batches while downloads are running.
- 🏷️ Downloaded badges stop re-checking the download drive while you scroll during a download; freshly finished tracks still update instantly.
- 📸 Two tracks finishing at the same moment can no longer trip a "File exists" error while writing the shared album cover to a network drive.

## 🌊 v0.1.8 (2026-07-13)

### ✨ Added

- 🌊 Waves now makes a splash on open (pun intended): a new launch sequence that also shows the version you are running.
- 📁 Path templates now show a live example: each "path & name" field in Settings → File organization displays the exact folders and file name a download would get, updating as you type. The example uses a built-in generic sample (Example Artist / Example Album), so it works before anything is downloaded; unknown `{tokens}` are highlighted so typos jump out.
- 🏷️ New "Want to know more about these paths and tags?" reference under Settings → File organization, below the path & name fields: every available `{token}`, grouped by category, with a short description, an example value, and a one-click copy button.

### 🔧 Changed

- 📜 Expanding an album now gently scrolls it up toward the top of the window, so the track list it reveals is on screen instead of below the fold. Rows already near the top stay where they are.
- ⬆️ A subtle TOP pill rides the top of any page you scroll down; one click glides you back to the top.
- 🎞️ Pages now scroll with more depth: rows fade in and out of frame at the top and bottom edges, with a subtle rolodex tilt as they cross, instead of being cut off hard at the chrome edge.
- 🗂️ The My Tidal tab now reopens in the category you left, exactly as you left it; pressing it again returns to Home (like Browse's second press).

### 🐛 Fixed

- 📰 The TIDAL Magazine tile no longer appears in Browse rows like Moods & Activities: it is editorial articles, so opening it always showed an empty page.
- ⚡ Expanding an album's track list is instant after the first time: track lists are now remembered for the session instead of re-fetched each time.
- 🏠 My Tidal opens instantly after launch: the Home shelves are remembered from your last session and shown immediately, then quietly refreshed in the background.
- 🌱 Home and the library lists stay current while the app runs: new favourites show up on their own, no restart needed.
- 📜 Scrolling or re-sorting the Playlists and Mixes tabs no longer re-downloads your entire collection for every page, so large collections stay snappy.
- 🔍 Search results no longer freeze the app while they appear: the cards are built in the background and the finished page appears all at once, same look as before.
- 🔁 Repeating a recent search shows its results instantly, popularity meters included.
- ▶️ Replaying a recent track or artist preview starts instantly instead of rebuilding the clip each time.
- 🎯 Going Back to an artist page now lands exactly where you left it: scroll position, expanded albums, expanded bio and top-tracks all come back, with no visible jump. Opening a different artist also starts at the top of the page instead of inheriting the previous page's scroll offset.
- 🏄 Switching to the Browse tab no longer stutters: the shelves are built in the background and the finished page appears all at once, same look as before.
- ⚡ Returning to Browse (and the My Tidal home) is instant: the cards stay alive while the tab is hidden, so switching back just shows them.
- 🖱️ Top-bar tabs (Browse, Search, My Tidal, Settings) now respond the instant you click: the tube starts expanding on the click itself with the static riding on top, and the outgoing tab collapses in half the time. Same look, no dead time.
- 📺 Video playback in installed builds now really selects a resolution: videos start at the best your connection and Video-quality setting allow, the quality menu works, and seeking jumps straight to the chosen spot. Video downloads get the same fix.

## 📦 v0.1.7 (2026-07-12)

### ✨ Added

- 🍺 Waves is now installable on macOS via Homebrew:

  ```bash
  brew tap iamprivacy/waves && brew install --cask waves
  ```

  Keeping it current is a `brew upgrade`, or just the usual Update & restart button in the app.

- 🐧 Linux releases now also ship as an AppImage: one file, no unzipping, no install step, just download and run. The built-in one-click updater works on it too, replacing the file in place.

### 🔧 Changed

- 📁 Waves settings now live where your system expects them: Application Support on macOS and AppData on Windows (Linux keeps ~/.config). Existing settings, login and history move over automatically the first time the new version starts; nothing needs to be set up again.
- 📦 Copies of Waves installed through a package manager (like the Homebrew tap) update through that package manager instead: the familiar Update & restart button simply runs its one-line upgrade for you, same one click, and the manager's records stay correct. Direct downloads keep the built-in updater, unchanged.

### 🐛 Fixed

- 🏷️ Album, playlist and mix DOWNLOADED badges now reflect what is actually on disk, not just the current session: an album downloaded on its own now shows DOWNLOADED the same as one downloaded as part of a full discography, and playlists and mixes pick up the badge too. Collapsed album rows and browse shelf cards get the same accurate badge as an opened page, learned locally the first time Waves sees that album, playlist or mix, so it never needs a network check to answer.

## 💾 v0.1.6 (2026-07-12)

### ✨ Added

- ⬇️ Waves now remembers what it has downloaded. Track and video download buttons show DOWNLOADED across sessions when the file from an earlier download still exists on disk, and clicking a DOWNLOADED button will not re-download the file. Album, artist, playlist and mix downloads skip the tracks you already have too (marked HAVE in the queue), fetching only what is missing. The check follows the real file: delete or move it and the item downloads again. Quality upgrades still work: raise the audio quality setting and copies below it show DOWNLOAD again, and downloading replaces the old file in place with the better one.

  An honest limitation for now: this only knows about downloads made from this version onward, so files downloaded before updating, or with other tools, are not detected. Proper library detection and management (recognizing music already in your folders, whatever put it there) is coming in a bigger future update, no ETA yet.

### 🐛 Fixed

- 📊 Download progress bars no longer flash or jump backward when a track finishes while others are still downloading or finalizing.
- 🖥️ Checking whether a track is already downloaded no longer risks freezing the app when the download folder sits on a network drive that has dropped or gone slow: the check now answers from a short‑lived cache and refreshes in the background, so the interface stays responsive either way.

## ⚡ v0.1.5 (2026-07-11)

<p align="center">
  <strong>🌊 If you enjoy Waves, a star on this repo goes a long way, and if you'd like to help me afford to keep developing it, consider donating.</strong>
</p>

<p align="center">
  <a href="https://www.buymeacoffee.com/iamprivacy"><img src="https://img.shields.io/badge/-Buy%20me%20a%20coffee-ffdd00?style=flat&logo=buy-me-a-coffee&logoColor=black" alt="Buy Me a Coffee"></a>
</p>

### 🔧 Changed

- 📋 The Completed section of the download queue now lists the most recently finished item first, oldest at the bottom.
- 🔍 Pressing Search puts the cursor in the search bar, ready to type. Any previous query is selected, so typing starts a fresh search.
- 🎤 Artist pages show the first 5 top tracks with a SHOW ALL link for the rest, and the Top tracks, Albums and EPs & Singles sections are now collapsible. A collapsed section stays collapsed on every artist page until you reopen it, so album hunters skip the top tracks for good.
- ✨ Download bars no longer freeze at 100% while the final steps run (merging, converting, tagging): the dots twinkle softly until the item is actually done. Applies to the queue rows, download buttons and the small hover-card bars alike.

### 🐛 Fixed

- 🖥️ Starting a download no longer spikes the CPU to 100% (most visible on Windows). TLS setup for the segment connections is now done once and shared, and at most 10 connections open at a time instead of up to 60 at once.
- 🎬 The connection check that picks the starting video quality no longer counts connection setup time as slowness, so slower machines get a more accurate (often higher) starting quality.
- ⚡ The Download button reacts instantly. The safety check that verifies the download folder is reachable used to run before anything appeared on screen, which could freeze the click for several seconds when the folder lives on a network drive. The queue row now appears immediately and the check runs in the background; an unreachable folder still shows the same warning with Try again.

## 🔄 v0.1.4 (2026-07-11)

<p align="center">
  <strong>🌊 If you enjoy Waves, a star on this repo goes a long way, and if you'd like to help me afford to keep developing it, consider donating.</strong>
</p>

<p align="center">
  <a href="https://www.buymeacoffee.com/iamprivacy"><img src="https://img.shields.io/badge/-Buy%20me%20a%20coffee-ffdd00?style=flat&logo=buy-me-a-coffee&logoColor=black" alt="Buy Me a Coffee"></a>
</p>

### ✨ Added

- 🔔 A new-release toast that carries the whole update. When the automatic update check (opt-in) finds a new version, a small notice appears at the bottom of the window and INSTALL runs everything right there: download, signature verification and staging with live progress, then a RESTART NOW prompt (CANCEL available mid-install, RETRY on failure). It stays up until you act on it or dismiss it, and returns at every launch until you do (per version). A manual check from Settings never toasts (you are already on the updater card), and the gold update notice in the status bar plus the full Settings updater card remain as before.
- 🛡️ Downloads now check for FFmpeg up front. Starting a download without FFmpeg used to quietly produce degraded files (FLAC left in its stream container, no video conversion, track lengths unrepaired so strict players show 0:00). The download is now held while a dialog explains the problem, with "Set up FFmpeg" jumping straight to the one-click install and "Continue anyway" available for those who want the files regardless (asked once per session).
- ▶️ Artist cards in My Tidal now carry the same compact preview player as the Browse cards, centered under the artist's name: one click plays that artist's top track (with the elapsed counter and STOP control), filling the blank strip at the bottom of each card.

### 🔧 Changed

- 🔍 The Search tab now remembers where you were. Coming back from My Tidal or Browse returns you to the exact page you left, artist page, expanded album, scroll position and all, instead of dropping you back on the results list. Pressing Search again while already on it starts a fresh, blank search, the same two-step behaviour the Browse tab already had.
- 📊 Album, playlist, and discography progress bars now move continuously. They used to sit still and then jump each time a whole track finished; the bar (and the matching media buttons) now creeps along with the tracks that are currently downloading, and the "N/total tracks" count only ticks up when a track really completes.
- 🛠️ When FFmpeg is missing, Waves says so instead of quietly degrading. Without FFmpeg it cannot extract FLAC, convert video, or repair track length, so it now warns once per session, and it records which FFmpeg it used (managed, custom, system, or none) in your settings file so a pasted config shows whether FFmpeg was available. The FFmpeg path field itself is left untouched.
- 💡 The dot-matrix progress pill's status text now sits on a dark backing plate, so it stays readable as the lit cells fill in behind it (updater cards, FFmpeg installs, and the new update toast all share the fix).

### 🐛 Fixed

- 🔥 Downloads no longer peg the CPU or freeze the window. Every track segment was opening a brand-new encrypted connection (a fresh TLS handshake) instead of reusing one, so a high-resolution album fanned across many parallel connections became a storm of handshakes. Handshake crypto runs across all cores, so it could drive CPU to 100% and make the app unresponsive the moment a download started (worse the more cores a machine has). Segments now reuse pooled connections, which cuts the download CPU cost by roughly 16x and downloads faster, on any hardware and without lowering the parallelism.
- 🌡️ Starting an album download no longer causes a brief CPU spike. The connection pool that keeps downloads cheap was being rebuilt from scratch for every queued album, so each one began with a burst of encrypted-connection handshakes (CPU jumps to 100% for a moment, then settles). The warm pool is now shared across the whole session, so only the very first download pays that cost.
- 🎧 Downloaded tracks now carry their real length everywhere. Tracks delivered as segmented streams (most AAC and lossless files) were saved in a container whose header reported a length of zero, so strict players (for example Winamp) showed 0:00 and refused to play them, even though VLC played the same file fine. Waves now rebuilds the container after downloading so the correct duration is written, keeping the audio bit for bit identical (this needs FFmpeg).
- 💾 Downloads to a network drive or NAS no longer fail one by one after the drive drops off (for example when the laptop lid was closed). Waves now checks that the download folder really accepts writes before starting, and if the same share simply reconnected under a new name (macOS often remounts it with a "1" suffix) it follows the live mount automatically. If the folder is genuinely unreachable, the download is held and a dialog explains what happened, with "Try again" (after reconnecting) and "Choose a new location" actions, instead of a wall of silently failed tracks.
- 📂 Finished downloads now tuck themselves into the queue's Completed section even while the queue panel is closed. The 5-second tidy-up only ran while a row was on screen, so opening the queue after a big batch made every finished row fold away at once, in one distracting cascade. Rows you are watching still fade out gently; everything else is already in place when you look.
- 🪟 Windows: downloads no longer flash open a command-prompt window for every track. FLAC extraction and format conversion run ffmpeg as a child process, and the flag that keeps that process windowless was being discarded before the process started, so a console popped up (and vanished) for each one. It now runs fully hidden.

## 🔄 v0.1.3 (2026-07-10)

<p align="center">
  <strong>🌊 If you enjoy Waves, a star on this repo goes a long way, and if you'd like to help me afford to keep developing it, consider donating.</strong>
</p>

<p align="center">
  <a href="https://www.buymeacoffee.com/iamprivacy"><img src="https://img.shields.io/badge/-Buy%20me%20a%20coffee-ffdd00?style=flat&logo=buy-me-a-coffee&logoColor=black" alt="Buy Me a Coffee"></a>
</p>

### 🐛 Fixed

- 🔄 Browse now keeps up with TIDAL. The landing page (New tracks, New albums, Top playlists, and the rest) used to load once per session and then stay frozen, so "new" rows drifted days out of date, and scrolling deep into a row could surface newer tracks below older ones. Every return to the Browse tab now quietly re-checks the editorial pages and repaints only what actually changed, and an open row listing snaps to the fresh ordering too.

## 🩺 v0.1.2 (2026-07-09)

<p align="center">
  <strong>🌊 If you enjoy Waves, a star on this repo goes a long way, and if you'd like to help me afford to keep developing it, consider donating.</strong>
</p>

<p align="center">
  <a href="https://www.buymeacoffee.com/iamprivacy"><img src="https://img.shields.io/badge/-Buy%20me%20a%20coffee-ffdd00?style=flat&logo=buy-me-a-coffee&logoColor=black" alt="Buy Me a Coffee"></a>
</p>

### ✨ Added

- 🩺 A new Diagnostics section in Settings, built around a privacy-guarded logger: every line is scrubbed of identity information (username, file paths, network addresses, account details, tokens) the moment it's written, not filtered afterward, so nothing sensitive is ever on disk to begin with. Turn on "Verbose diagnostics", reproduce the problem, then click "Export report" for a single text file that's safe to attach to a public bug report. An optional switch also hides what you searched for and the names of tracks, albums and artists. Verbose mode also watches for interface freezes and records what the app was doing when one happened. See the [README](README.md#diagnostics) for how the guard works.
- 🩺 Waves now keeps a crash log. If the app ever crashes or freezes, the technical details land in `crash.log` inside the Waves config folder (`~/.config/Waves` on macOS and Linux, `%USERPROFILE%\.config\Waves` on Windows), and the bug report form explains where to find it. The log holds only version numbers and stack traces of the app's own code, never personal data.

### 🔧 Changed

- 🎛️ The FFmpeg card in Settings now mirrors the Updates card: status and actions on the left, and a new "Check for updates automatically" toggle (every launch or once a day) on the right. Like app updates, the automatic check is off by default, only notifies you, and sends none of your data.

### 🐛 Fixed

- 🎨 The Settings section icons now all share the same green. The FFmpeg section's icon still works as a status light (red when missing, yellow for a system copy), but a healthy managed install now reads as the standard accent instead of a slightly minty green that made it stand out.

- 🌊 A settings section with an odd number of on/off tiles no longer leaves a blank spot in the grid; the empty slot is now filled with a calm ASCII-wave tile in the Waves style.

- 🪟 The window can no longer be resized narrow enough to cut off the left side of the top bar. The minimum window width now follows what the top bar actually needs.

- 📥 Adding several artists to "download discography" at once no longer crashes or stalls the app. The release scans now run one after another instead of all at the same time, so you can queue as many artists as you like and they simply line up. Downloads themselves still run in parallel as before.

- 🖼️ Cover art the app has seen before now paints straight from the local cache on launch (the Browse landing page no longer flashes the loading placeholder while every cover is re-checked against the server).

## 🌊 v0.1.1 (2026-07-07)

<p align="center">
  <strong>🌊 If you enjoy Waves, a star on this repo goes a long way, and if you'd like to help me afford to keep developing it, consider donating.</strong>
</p>

<p align="center">
  <a href="https://www.buymeacoffee.com/iamprivacy"><img src="https://img.shields.io/badge/-Buy%20me%20a%20coffee-ffdd00?style=flat&logo=buy-me-a-coffee&logoColor=black" alt="Buy Me a Coffee"></a>
</p>

### ✨ Added

- 🏠 My Tidal opens on a new "Home" tab: a browse-style landing for your own account. A "Recently added" section previews your newest albums and tracks; clicking a card opens that album, and clicking a shelf heading ("Recent albums" or "Recent tracks") jumps to that tab sorted newest-first.
- ↕️ My Tidal can now be sorted (recently added, name, release date, or artist) with an ascending/descending toggle, the same control as the Search page.
- 👤 Opening an artist from inside My Tidal now shows an artist page scoped to your library: only the albums and tracks you have saved, not their whole catalogue. A "View full artist page" link opens their complete catalogue when you want it.
- 🖼️ The embedded cover art and the separate cover.jpg can now use different sizes. Open "Separate cover.jpg size" under Cover size in Settings, Metadata and artwork.
- 🎵 A separate cover.jpg can now be saved for single-track downloads too, not only full albums. Turn on "Also save for single tracks" under Save cover.jpg in Settings, Metadata and artwork (off by default, so nothing changes unless you ask for it).

### 🔧 Changed

- 🎛️ The FFmpeg download progress (in the setup pop-up and in Settings) now uses the same LED dot-matrix progress bar as the in-app updater.
- 🟩 The LED cells across the app (the popularity meter, the download and playback progress bars, and the FFmpeg and updater bars) now render as sharp squares instead of slightly rounded blocks.
- 🏷️ The "Clean album-artist tag" setting is now "Clean Album Artist", with a shorter description that fits its tile.
- 🗂️ My Tidal shows artists as a compact card grid instead of tall rows, so more fit on screen at once.
- 📁 Waves no longer starts with a default download folder. New installs pick a folder before the first download, with a prompt that links straight to the setting. Anyone who already has a folder set (including the previous default) keeps it. Users still on that old default are asked, before the download runs, whether to keep it or choose a new location: keeping it continues the download and settles the question, while choosing a new location holds the download so they can set a folder they can find, then start it again.

### 🐛 Fixed

- 🔁 A rare server response (an empty but otherwise successful segment) can no longer make a download re-fetch the same track over and over without end. Each part is now downloaded once, and the progress bar still settles at 100%.
- ⚡ A download no longer drives high CPU usage. The animated LED progress fills (on the download button, the queue rows, and the FFmpeg and updater bars) were redrawing the whole window on every screen refresh while they were active, which could push a CPU core to full load for the length of a download. They now animate on a shared lower-rate timer, so they look the same while using a small fraction of the CPU.
- ✅ A download that writes no file (for example on an account without an active TIDAL subscription, where playback is refused) now correctly shows as failed with a retry option, instead of incorrectly showing as downloaded.
- 🎨 The FFmpeg card's "Check for updates" button now uses the standard green button style instead of a grey outline, and "Remove" now uses the red danger style, matching buttons everywhere else in the app.
- 🌊 My Tidal no longer flashes a placeholder when you open it. It keeps the shelves it has already loaded and shows them instantly on return, and while a category is still loading (or is genuinely empty) the pane simply shows the ambient wave background, with no card or glyph that appears for a beat and fades away.
- 🎯 Opening a track's album no longer scrolls the page down to the track; it now lands already positioned on it, with no visible jump.

## 🚀 v0.1.0 (2026-07-06)

<p align="center">
  <strong>🌊 If you enjoy Waves, a star on this repo goes a long way, and if you'd like to help me afford to keep developing it, consider donating.</strong>
</p>

<p align="center">
  <a href="https://www.buymeacoffee.com/iamprivacy"><img src="https://img.shields.io/badge/-Buy%20me%20a%20coffee-ffdd00?style=flat&logo=buy-me-a-coffee&logoColor=black" alt="Buy Me a Coffee"></a>
</p>

First public release of Waves: a native desktop app for saving music from your own TIDAL account for offline listening, built on the Tidal-DL-NG engine (actively maintained as Tidaler) with a from-scratch Qt Quick interface.

### ✨ Added

- 🖥️ A native dark "console" UI (PySide6 / Qt Quick): no web view, no Electron.
- 🧭 Browse: TIDAL's editorial front page (New Arrivals, TIDAL Rising, genres, moods, decades) rendered art-first, with hover Preview / Download controls and quality badges throughout.
- 🔍 Search-first navigation: one field searches artists, albums, tracks, videos, playlists, and mixes, and resolves pasted tidal.com links directly.
- ▶️ Full seekable track previews streamed from your own account, with a now-playing bar that follows you across views.
- 🎬 A built-in video player with seek, keyboard controls, and a per-video quality picker (up to 1080p) that can switch resolution mid-stream.
- 🎤 Artist pages (bio, discography, EPs and singles, top tracks) with one-click whole-artist saving: per-source toggles, most-complete-edition selection, and features/compilations limited to the artist's own tracks.
- 🧩 "Best of both" album merging: when editions differ in tracks and quality, the download takes each song at its best, matched by ISRC first, otherwise only on an identical title and near-identical length.
- 📚 A Plex-friendly library layout by default (Artist/[Year] Album/...), a clean album-artist tagging mode, and an explicit/clean version preference.
- ❤️ My TIDAL: favorite albums, tracks, artists, videos, playlists, and mixes with smooth virtualized scrolling.
- 📥 A grouped download queue (Completed / Downloading / Queued) with live per-track progress and per-album / per-artist roll-ups.
- 🛠️ One-click managed FFmpeg: Waves downloads a checksum-verified build for your OS and CPU, with a colour-coded status light in Settings.
- 🔄 Opt-in in-app updates: signed releases (Ed25519, fail-closed verification) installed from Settings with a one-click restart. Update checks are off by default and send no user data.
- 💾 Persistent page and artwork caches so previously seen pages render instantly, even on a fresh launch.

### 🐛 Fixed

- 🪟 FFmpeg jobs on Windows (FLAC extraction, video conversion, previews) run fully hidden, with no console windows stealing focus mid-download.
- ⚛️ Interrupted downloads can no longer leave a half-written file in the library: finished files are swapped into place atomically.
- 🛑 Downloads stop instantly on cancel or quit instead of waiting on a network read.
