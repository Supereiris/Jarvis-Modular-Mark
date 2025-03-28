import tempfile
import threading
import time
import pyttsx3
import pygame
import wave
import pyaudio
from pydub import AudioSegment
import json
import config
from localization import _

engine = pyttsx3.init()
pygame.mixer.init()
voices = engine.getProperty('voices')

matching_voice = None
for voice in voices:
    if voice.id.lower() == config.VOICE_KEY.lower():
        matching_voice = voice.id
        break
if matching_voice is None:
    matching_voice = r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech\Voices\Tokens\MSTTS_V110_enUS_DavidM"

engine.setProperty('voice', matching_voice)
speech_channel = pygame.mixer.Channel(1)

vb_cable_index = None
if config.VB_CABLE:
    import speech_recognition as sr
    for i, device in enumerate(sr.Microphone.list_microphone_names()):
        if config.VB_CABLE_DEVICE.lower() in device.lower():
            vb_cable_index = i
            break
    if vb_cable_index is None:
        print("Error: VB-CABLE Input not found! Disabling VB-Cable output.")
        config.VB_CABLE = False

def speak(text, rate=170, pitch_factor=0.98):
    """Convert text to speech and play asynchronously.
    
    If config.VB_CABLE is True and the VB-Cable device is found,
    output the audio via VB-Cable using PyAudio.
    Otherwise, play using pygame's mixer.
    """
    def _speak():
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        temp_filename = temp_file.name
        temp_file.close()

        engine.setProperty('rate', rate)
        engine.save_to_file(text, temp_filename)
        engine.runAndWait()

        sound = AudioSegment.from_file(temp_filename, format="wav")
        new_sample_rate = int(sound.frame_rate * pitch_factor)
        pitched_sound = sound._spawn(sound.raw_data, overrides={"frame_rate": new_sample_rate})
        pitched_sound = pitched_sound.set_frame_rate(44100)
        pitched_sound.export(temp_filename, format="wav")

        if config.VB_CABLE and vb_cable_index is not None:
            with wave.open(temp_filename, 'rb') as wf:
                audio = pyaudio.PyAudio()
                stream = audio.open(
                    format=audio.get_format_from_width(wf.getsampwidth()),
                    channels=wf.getnchannels(),
                    rate=wf.getframerate(),
                    output=True,
                    output_device_index=vb_cable_index
                )
                data = wf.readframes(1024)
                while data:
                    stream.write(data)
                    data = wf.readframes(1024)
                stream.stop_stream()
                stream.close()
                audio.terminate()
        else:
            playback = pygame.mixer.Sound(temp_filename)
            speech_channel.play(playback)
            while speech_channel.get_busy():
                time.sleep(0.1)

    threading.Thread(target=_speak, daemon=True).start()

def stop_speech():    
    if speech_channel.get_busy():
        speech_channel.stop()
        

if config.SPEECH_ENGINE == "vosk":
    from vosk import Model, KaldiRecognizer
    import pyaudio

    vosk_model = Model(config.VOSK_MODEL_PATH)
    recognizer = KaldiRecognizer(vosk_model, 16000)
    mic = pyaudio.PyAudio()
    stream = mic.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=16000,
        input=True,
        frames_per_buffer=8192
    )

    def process_audio(is_t_pressed):
        """
        Capture audio while the T key is held.
        Return the transcribed text using Vosk.
        """
        audio_frames = []
        while is_t_pressed():
            data = stream.read(4096, exception_on_overflow=False)
            audio_frames.append(data)
        if audio_frames:
            for frame in audio_frames:
                recognizer.AcceptWaveform(frame)
            result = json.loads(recognizer.FinalResult())
            return result.get("text", "")
        return None

    def close_audio():
        stream.stop_stream()
        stream.close()
        mic.terminate()

elif config.SPEECH_ENGINE == "speech_recognition":
    import speech_recognition as sr

    recognizer = sr.Recognizer()
    microphone = sr.Microphone()

    with microphone as source:
        recognizer.adjust_for_ambient_noise(source, duration=0.5)

    def process_audio(is_t_pressed):
        """
        Capture audio while the T key is held, mimicking Vosk's behavior.
        Records shorter 0.25-second chunks to reduce latency.
        Combines the recorded chunks and transcribes the result using SpeechRecognition.
        """
        audio_chunks = []
        with microphone as source:
            while is_t_pressed():
                try:
                    chunk = recognizer.record(source, duration=0.25)
                    audio_chunks.append(chunk)
                except Exception as e:
                    print(_("Error capturing audio:"), e)
        if audio_chunks:
            combined_raw = b"".join(chunk.get_raw_data() for chunk in audio_chunks)
            sample_rate = audio_chunks[0].sample_rate
            sample_width = audio_chunks[0].sample_width
            combined_audio = sr.AudioData(combined_raw, sample_rate, sample_width)
            try:
                text = recognizer.recognize_google(combined_audio)
                return text
            except sr.UnknownValueError:
                return ""
            except sr.RequestError as e:
                print(_("Could not request results; {0}").format(e))
                return ""
        return None

    def close_audio():
        pass

elif config.SPEECH_ENGINE == "faster_whisper":
    from faster_whisper import WhisperModel
    import pyaudio
    import wave
    import io
    import tempfile
    import os
    import torch

    # Determine the computation device (use GPU if available)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    compute_type = "float16" if device == "cuda" else "int8"
    # Initialize the Whisper model (choose an appropriate model size, e.g. "small", "base", "medium", etc.)
    model = WhisperModel("small", device=device, compute_type=compute_type)

    # Initialize PyAudio and open the microphone stream
    mic = pyaudio.PyAudio()
    stream = mic.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=16000,
        input=True,
        frames_per_buffer=8192
    )

    def process_audio(is_t_pressed):
        """
        Capture audio while the T key is held.
        Convert the captured audio frames into a temporary WAV file,
        then transcribe it using fast-whisper.
        Returns the transcribed text.
        """
        audio_frames = []
        while is_t_pressed():
            data = stream.read(4096, exception_on_overflow=False)
            audio_frames.append(data)
        if audio_frames:
            # Combine the audio frames and convert them to a WAV file in memory
            audio_data = b"".join(audio_frames)
            wav_buffer = io.BytesIO()
            with wave.open(wav_buffer, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(mic.get_sample_size(pyaudio.paInt16))
                wf.setframerate(16000)
                wf.writeframes(audio_data)
            wav_bytes = wav_buffer.getvalue()

            # Write the WAV bytes to a temporary file
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp.write(wav_bytes)
                tmp_filename = tmp.name

            try:
                # Transcribe the audio file using fast-whisper.
                # This returns a list of segments and additional info.
                segments, info = model.transcribe(tmp_filename)
                # Combine the segments to form the full transcript.
                transcript = " ".join([segment.text for segment in segments])
                return transcript
            finally:
                os.remove(tmp_filename)
        return None

    def close_audio():
        stream.stop_stream()
        stream.close()
        mic.terminate()

else:
    raise ValueError("Invalid speech engine specified in config.ini. Choose 'vosk' or 'speech_recognition'.")
