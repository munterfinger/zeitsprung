from bs4 import BeautifulSoup
from datetime import datetime, timezone
from io import BytesIO
from json import loads
from pandas import DataFrame
from pathlib import Path
from pydub import AudioSegment
from requests import get
from time import sleep
from zeitsprung.base import Base
from zeitsprung.database import SQLiteEngine


class Scraper(Base):
    """Class for scraping and preprocessing the data from 'www.zeitsprung.fm'."""

    def __init__(self, data_folder: str = 'data', update_interval: int = 24*60*60, reset: bool = False, verbose: bool = True) -> None:

        super().__init__(verbose)
        self.created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        self.data_folder = Path(data_folder)
        self.db = SQLiteEngine(self.data_folder / 'zeitsprung.db')
        self.update_interval = update_interval
        self.verbose = verbose

        if (self.data_folder / 'zeitsprung.db').exists() and reset:
            self._print(f"Overwriting existing directory structure in '{data_folder}'.")
            Path(data_folder).mkdir(parents=True, exist_ok=True)
            (Path(data_folder) / 'audio').mkdir(parents=True, exist_ok=True)
            self.db.setup_schema()

        elif (self.data_folder / 'zeitsprung.db').exists() and not reset:
            self._print(f"Binding to existing directory structure in '{data_folder}'.")

        else:
            self._print(f"Creating directory structure in '{data_folder}'.")
            Path(data_folder).mkdir(parents=True, exist_ok=True)
            (Path(data_folder) / 'audio').mkdir(parents=True, exist_ok=True)
            self.db.setup_schema()

        self.current_episode = self.db.query_last_episode_id()


    def __str__(self):
        return f"Scraper created at '{self.created_at}' with db connection to '{self.db.db_file}', current episode is 'ZS{self.current_episode}'."

    def get_episode_meta(self, i: int) -> list:
        url = f"https://www.zeitsprung.fm/podcast/zs{'0'+str(i) if i < 10 else str(i)}/"
        self._print(f'Requesting meta data of episode {i}: {url}')
        html_doc = get(url)
        if html_doc.status_code == 200:
            soup = BeautifulSoup(html_doc.content, 'html.parser')
            script_content = loads(soup.find("script").contents[0])
            title = soup.find('title').get_text(strip = True).split(":")
            return [
                i,
                datetime.fromisoformat(self.search_key('datePublished', script_content['@graph'])),
                datetime.fromisoformat(self.search_key('dateModified', script_content['@graph'])),
                title[0],
                title[1][1:],
                soup.find("meta", {"property": "og:description"}).get('content'),
                soup.find("meta", {"property": "og:url"}).get('content'),
                None if soup.find("ul", {"class":"episode_download_list"}) is None else soup.find("ul", {"class":"episode_download_list"}).find_all('a')[0].get('href')
            ]
        else: #html_doc.status_code == 404:
            return None

    @staticmethod
    def search_key(key, dict_obj):
        for entry in dict_obj:
            if key in entry:
                return(entry[key])

    def get_episode_audio(self, url: str) -> AudioSegment:
        if url is not None:
            self._print(f"Fetching audio file from {url}")
            audio_mp3 = BytesIO(get(url, allow_redirects=True).content)
            audio = AudioSegment.from_file(audio_mp3)
            return audio
        else:
            self._print(f'No audio file available for this episode.')
            return None

    def save_episode_audio(self, audio: AudioSegment, file_name: str) -> None:
        self._print(f"Exporting audio sequence to file '{file_name}'")
        audio.export(file_name, format="wav")

    def run(self) -> None:
        while True:
            meta_row = self.get_episode_meta(self.current_episode + 1)
            if meta_row is not None:
                self.db.insert_meta_row(meta_row)
                audio = self.get_episode_audio(meta_row[7])
                if audio is not None:
                    audio_row = [
                        self.current_episode + 1,
                        self.data_folder / 'audio' / f'{str(self.current_episode + 1).zfill(3)}.wav',
                        round(audio.duration_seconds),
                        audio.frame_rate,
                        audio.frame_width
                    ]
                    self.save_episode_audio(audio, audio_row[1])
                    self.db.insert_audio_row(audio_row)
                self.current_episode += 1
            else:
                self._print(f"Episode not yet published, pausing for {int(self.update_interval/(60*60))} hours.")
                sleep(self.update_interval)
