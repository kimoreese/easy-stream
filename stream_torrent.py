import libtorrent as lt
import time
import vlc
import sys
import os

def stream_torrent(magnet_link):
    ses = lt.session({'listen_interfaces': '0.0.0.0:6881'})

    try:
        params = lt.parse_magnet_uri(magnet_link)
    except Exception as e:
        print(f"Error parsing magnet link: {e}")
        print("Please ensure you have entered a valid magnet link.")
        return

    params.save_path = '.'
    h = ses.add_torrent(params)

    print("Fetching torrent metadata...")
    while not h.has_metadata():
        time.sleep(0.1)
    print("Metadata fetched.")

    ti = h.get_torrent_info()
    if not ti:
        print("Failed to get torrent info.")
        return

    files = ti.files()
    video_files = []
    print("\nFiles in torrent:")
    for i, f in enumerate(files):
        print(f"{i+1}. {f.path} ({round(f.size / (1024*1024), 2)} MB)")
        # Basic video file extension check
        if f.path.lower().endswith(('.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv')):
            video_files.append((i, f.path, f.size))

    if not video_files:
        print("\nNo video files found in this torrent.")
        return

    if len(video_files) == 1:
        choice_index = 0
        print(f"\nAutomatically selecting the only video file: {video_files[0][1]}")
    else:
        while True:
            try:
                choice = input(f"\nEnter the number of the video file you want to stream (1-{len(video_files)}): ")
                choice_index = int(choice) - 1
                # Find the original index from the filtered video_files list
                original_file_index = -1
                selected_file_path = ""
                for i, (orig_idx, path, size) in enumerate(video_files):
                    if i == choice_index:
                        original_file_index = orig_idx
                        selected_file_path = path
                        break

                if original_file_index != -1:
                    break
                else:
                    print(f"Invalid selection. Please enter a number between 1 and {len(video_files)}.")
            except ValueError:
                print("Invalid input. Please enter a number.")

    # Prioritize the selected file
    # The file_index in file_priorities refers to the index in the original ti.files() list
    file_index_to_stream = video_files[choice_index][0]

    # Initialize priorities: lt.download_priority.dont_download is 0
    # lt.download_priority.default_priority is 4
    # lt.download_priority.top_priority is 7
    priorities = [lt.download_priority.dont_download] * len(files)
    # Set highest priority for the selected file
    priorities[file_index_to_stream] = lt.download_priority.top_priority
    h.file_priorities(priorities)

    # Set download mode to sequential for the selected file to enable streaming
    # Note: libtorrent itself doesn't have a direct per-file sequential download flag after adding the torrent.
    # We rely on prioritizing the first and last pieces of the *selected* file to facilitate streaming.
    # This is a common approach. Full sequential download for a specific file is complex to manage directly.

    # Prioritize first and last pieces of the selected file
    # This helps start playback faster
    file_offset = files[file_index_to_stream].offset
    file_size = files[file_index_to_stream].size

    # Calculate piece indices for the start and end of the file
    # Piece length is part of torrent_info
    piece_length = ti.piece_length()

    start_piece = int(file_offset / piece_length)
    # For the end piece, we need to consider the file's end position within the torrent data
    end_piece_byte_offset = file_offset + file_size -1
    end_piece = int(end_piece_byte_offset / piece_length)

    h.piece_priority(start_piece, 7) # Max priority
    h.piece_priority(end_piece, 7)   # Max priority

    # If it's a multi-file torrent, and we selected a file, its path is relative to the torrent root
    # If it's a single-file torrent, the name is usually the torrent name
    file_to_stream_path = os.path.join(params.save_path, files[file_index_to_stream].path)

    # Ensure the directory for the file exists
    os.makedirs(os.path.dirname(file_to_stream_path), exist_ok=True)

    print(f"\nStarting download for: {files[file_index_to_stream].path}")
    print(f"Streaming will begin shortly. File path: {file_to_stream_path}")

    instance = vlc.Instance('--no-xlib') # --no-xlib for headless environments if needed, or remove
    player = instance.media_player_new()

    # Wait for the file to be created and have some data
    # This is a simplified check; more robust would be to check piece availability
    while not os.path.exists(file_to_stream_path) or os.path.getsize(file_to_stream_path) < piece_length * 2 : # Wait for at least 2 pieces
        s = h.status()
        print(f'\rDownloading: {s.progress*100:.2f}% | Peers: {s.num_peers} | Download Speed: {s.download_rate/1000:.2f} kB/s | Path: {file_to_stream_path} | Size: {os.path.exists(file_to_stream_path) and os.path.getsize(file_to_stream_path) or 0} bytes', end='')
        sys.stdout.flush()
        if s.state == lt.torrent_status.seeding: # If it's seeding, all data is there
            break
        if not h.is_valid(): # If torrent becomes invalid
            print("\nTorrent is no longer valid.")
            return
        if s.errc:
             print(f"\nError downloading torrent: {s.errc.message()}")
             return
        time.sleep(1)
    print("\nFile has some data, attempting to stream...")

    media = instance.media_new(file_to_stream_path)
    player.set_media(media)
    player.play()

    # Keep the script running while VLC is playing and torrent is downloading
    try:
        while True:
            time.sleep(1)
            s = h.status()
            player_state = player.get_state()

            # If player is stopped or errored, and torrent is not seeding, there might be an issue
            if player_state == vlc.State.Ended or player_state == vlc.State.Error:
                 print(f"\nVLC playback ended or encountered an error (State: {player_state}).")
                 if s.state != lt.torrent_status.seeding:
                     print("Torrent is not fully downloaded. Exiting.")
                 else:
                     print("Torrent fully downloaded. Exiting.")
                 break

            if s.state == lt.torrent_status.seeding:
                print("\rTorrent fully downloaded and seeding. Playback continues.", " "*20) # Extra spaces to clear line
                # If VLC is still playing, let it finish. If it stopped, we might break.
                if player_state != vlc.State.Playing and player_state != vlc.State.Paused:
                    print("VLC is not playing. Exiting.")
                    break
            else:
                print(f'\rDownloading: {s.progress*100:.2f}% | Peers: {s.num_peers} | DL Speed: {s.download_rate/1000:.2f} kB/s | VLC State: {player_state}', end='')
                sys.stdout.flush()

            if not h.is_valid():
                print("\nTorrent became invalid during download.")
                break
            if s.errc:
                print(f"\nError during download: {s.errc.message()}")
                break


    except KeyboardInterrupt:
        print("\nStreaming stopped by user.")
    finally:
        player.stop()
        ses.remove_torrent(h)
        print("Cleaned up torrent session.")
        # Attempt to remove partially downloaded file if not fully downloaded
        if h.status().state != lt.torrent_status.seeding and os.path.exists(file_to_stream_path):
            try:
                # Check if it's a single file torrent or multi-file
                if ti.num_files() == 1 and os.path.isfile(file_to_stream_path):
                    os.remove(file_to_stream_path)
                    print(f"Removed partially downloaded file: {file_to_stream_path}")
                elif ti.num_files() > 1:
                    # For multi-file torrents, the save_path is the torrent name, and files are inside
                    # We need to be more careful here. Let's remove the specific file.
                    if os.path.isfile(file_to_stream_path):
                         os.remove(file_to_stream_path)
                         print(f"Removed partially downloaded file: {file_to_stream_path}")
                    # Potentially remove the torrent's root directory if it's empty
                    torrent_root_dir = os.path.join(params.save_path, ti.name())
                    if os.path.isdir(torrent_root_dir) and not os.listdir(torrent_root_dir):
                        os.rmdir(torrent_root_dir)
                        print(f"Removed empty torrent directory: {torrent_root_dir}")

            except OSError as e:
                print(f"Error removing file/directory: {e}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        magnet = sys.argv[1]
    else:
        magnet = input("Please enter the magnet link: ")

    stream_torrent(magnet)
