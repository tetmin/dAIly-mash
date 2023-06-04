import modal
import requests
import openai
import os
import re
from datetime import datetime
import pytz
import base64
import json
import cloudinary.uploader
import tweepy
from dotenv import load_dotenv
import json

if modal.is_local():
    load_dotenv()

# Setup the Modal Labs image
image = modal.Image.debian_slim().poetry_install_from_file("pyproject.toml")
stub = modal.Stub(
    name="the-alium",
    image=image,
    secrets=[
        modal.Secret.from_name("toms-github-secret"),
        modal.Secret.from_name("twitter-secrets"),
        modal.Secret.from_name("mark-gnews-secret"),
        modal.Secret.from_name("toms-openai-secret"),
        modal.Secret.from_name("toms-cloudinary-secret"),
        modal.Secret.from_name("toms-respell-secret"),
    ],
)


def commit_new_blog_post(filename, content):
    # GitHub repository details
    owner = "tetmin"
    repo = "the-alium"
    path = f"_posts/{filename}"
    token = os.environ["GITHUB_TOKEN"]

    # GitHub API URL for this repository
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"

    # Your GitHub Personal Access Token
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    # Prepare the data for the API request
    data = {
        "message": "Create a new post",
        "content": base64.b64encode(
            content.encode()
        ).decode(),  # GitHub API requires the file content to be base64 encoded
        "branch": "main",
    }

    # Make the API request
    response = requests.put(url, headers=headers, data=json.dumps(data))


# Get some topical news article headlines
def get_news_articles(api_key, query, n_articles):
    url = f"https://gnews.io/api/v4/search?q={query}&max={n_articles}&token={api_key}"
    response = requests.get(url)
    data = response.json()

    titles = []

    if response.status_code == 200:
        articles = data.get("articles", [])
        for article in articles:
            titles.append(article.get("title"))
            print(f"Title: {titles[-1]}")
            print("--------------")
        return articles
    else:
        print("Failed to retrieve news articles.")
        return []


def get_datetime_for_frontmatter():
    # Get the current date and time in UTC
    now = datetime.now(pytz.utc)

    # Convert to the desired timezone
    desired_timezone = pytz.timezone("Europe/London")
    now = now.astimezone(desired_timezone)

    # Format the date and time using strftime()
    return now.strftime("%Y-%m-%d %H:%M:%S %z")


def get_date_for_filename():
    # Get the current date and time in UTC
    now = datetime.now(pytz.utc)

    # Convert to the desired timezone
    desired_timezone = pytz.timezone("Europe/London")
    now = now.astimezone(desired_timezone)

    # Format the date and time using strftime()
    return now.strftime("%Y-%m-%d")


# Clean up a filename for Jekyll
def clean_filename(filename):
    # Remove illegal characters
    cleaned = re.sub(r'[\\/:"\'\’\‘*?<>|]', "", filename)

    # Replace spaces with underscores
    cleaned = cleaned.replace(" ", "_")

    # Normalize case (lowercase)
    cleaned = cleaned.lower()

    # Limit length (optional)
    cleaned = cleaned[:255]  # Limit to 255 characters (adjust as needed)

    return cleaned


class Story:
    def __init__(self, original_title, title, content):
        self.original_title = original_title
        self.title = title
        self.content = content
        self.image_prompt = ""
        self.image_url = ""
        self.llm = ""

    def display(self):
        print(f"Title: {self.title}")
        print(f"image prompt: {self.image_prompt}")
        print(self.content)
        print("------------")

    def jekyll_file_content(self):
        return (
            f'---\ntitle: "{self.title}"\ndate: {get_datetime_for_frontmatter()}\nimage: {self.image_url}\nllm: {self.llm}\n---\n'
            f'![Alt Text]({self.image_url} "{self.image_prompt}")\n\n{self.content}'
        )

    def jekyll_file_name(self):
        return (
            f"{get_date_for_filename()}-{clean_filename(self.original_title)}.MARKDOWN"
        )

    def write_jekyll_file(self, path=""):
        if path:
            path = path + "/"
        file_name = f"{path}{self.jekyll_file_name()}"
        with open(file_name, "w") as f:
            f.write(self.jekyll_file_content())

    # Generate Jekyll post URL from Markdown filename
    def get_jekyll_post_url(self):
        # Extract the date and name from the filename
        match = re.match(
            r"(\d{4})-(\d{2})-(\d{2})-(.*)\.MARKDOWN", self.jekyll_file_name()
        )
        if not match:
            raise ValueError(f"Invalid filename: {self.jekyll_file_name()}")
        year = match.group(1)
        month = match.group(2)
        day = match.group(3)
        title = match.group(4)
        URL = f"https://www.thealium.com/{year}/{month}/{day}/{title}.html"
        print(URL)

        return URL


def get_completion(prompt, model="gpt-3.5-turbo"):
    messages = [{"role": "user", "content": prompt}]
    response = openai.ChatCompletion.create(
        model=model, messages=messages, temperature=0.7, max_tokens=1000
    )
    return response.choices[0].message["content"]


# Uses Respell for GPT-4 access
def get_respell_completion(title):
    response = requests.post(
        "https://api.respell.ai/v1/run",
        headers={
            # This is your API key
            "Authorization": "Bearer " + os.environ["RESPELL_TOKEN"],
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        data=json.dumps(
            {
                "spellId": "wd8KSLZz7WVCT5dh0ysas",
                "inputs": {
                    "title": title,
                    "dummy": "",
                },
            }
        ),
    )

    return response.json().get("outputs")


def get_moderation_flag(prompt):
    response = openai.Moderation.create(input=prompt)
    return response.results[0].flagged


def split_string(string):
    lines = string.split("\n", 1)  # Split the string at the first newline character
    title = lines[0]  # First line is considered as the title
    content = (
        lines[1] if len(lines) > 1 else ""
    )  # Rest of the string is considered as the content

    return re.sub(r"^#+\s*|\*\*|\*\s*", "", title), content


def get_existing_titles():
    # Send the GET request
    response = requests.get(
        "https://api.github.com/repos/tetmin/the-alium/contents/_posts"
    )

    names = []
    # If the request was successful, the status code will be 200
    if response.status_code == 200:
        # Load the JSON data from the response
        data = json.loads(response.text)

        # Print the name of each file in the directory
        for file in data:
            names.append(file["name"][11:-9])

    return names


def b_new_story(title):
    """Check to make sure we haven't done this story already."""
    # Get index of story titles already in repo
    existing_titles = get_existing_titles()

    # return match result
    return clean_filename(title) not in existing_titles


@stub.function()
def generate_post_respell(article):
    title = article.get("title")
    story = None
    if b_new_story(title) and not get_moderation_flag(prompt + title):
        try:
            new_story = get_respell_completion(title)
            new_title, content = split_string(new_story["story"])
            story = Story(title, new_title, content)
            story.llm = "ChatGPT-4"

            story.image_prompt = new_story["image_prompt"]
            story.image_url = new_story["image"]
            response = cloudinary.uploader.upload(story.image_url)
            story.image_url = response["secure_url"]
        except:
            print("Error generating story using Respell, switching to ChatGPT-3.5")
            story = generate_post(article)

    return story


@stub.function()
def generate_post(article):
    # Call ChatGPT to generate the stories
    title = article.get("title")
    story = None
    if b_new_story(title) and not get_moderation_flag(prompt + title):
        new_story = get_completion(prompt + title)
        new_title, content = split_string(new_story)
        story = Story(title, new_title, content)
        story.llm = "ChatGPT-3.5"

        # Get ChatGPT to generate a prompt for Dall-E to generate an image for each story
        story.image_prompt = get_completion(
            f"""Describe an image which could represent the below news headline using the following template format: [emotion][subject][action],photographic style. Ensure the description doesn't contain any violent, sexual or graphic words.

News Headline: Artificial intelligence denies plans for human extinction just a ‘publicity stunt’
Image Idea: Serious AI speaking at a podium, photographic style

News Headline: AI Blamed for Massive Unemployment, Robots Celebrate Victory
Image Idea: Excited Robots celebrating victory, photographic style

News Headline: Global leaders fear extinction from AI, but AI not sure who they are
Image Idea: Scared politicians searching for answers, photographic style

News Headline: {story.title}
Image Idea:"""
        )
        # check the image prompt is not flagged
        if get_moderation_flag(story.image_prompt):
            print(f"Image prompt failed moderation: {story.image_prompt}")
            story = None
            return story

        story.display()

        # now get the image itself
        try:
            response = openai.Image.create(
                prompt=story.image_prompt, n=1, size="512x512"
            )
            story.image_url = response["data"][0]["url"]
            response = cloudinary.uploader.upload(story.image_url)
            story.image_url = response["secure_url"]
        except openai.error.OpenAIError as e:
            print(f"Image generation failed for: {story.image_prompt}")
            print(e.error)

    return story


def tweet_article(story):
    client = tweepy.Client(
        consumer_key=os.environ["consumer_key"],
        consumer_secret=os.environ["consumer_secret"],
        access_token=os.environ["access_token"],
        access_token_secret=os.environ["access_token_secret"],
    )

    response = client.create_tweet(
        text=f"{story.title}\n\n{story.get_jekyll_post_url()}?nolongurl"
    )


# Specify the query filter for articles (e.g., "artificial intelligence")
query = "artificial intelligence"

prompt = f"""You are a staff writer at The Daily Mash. Write a parody of the provided original news headline in the style of The Daily Mash. Ensure any proper names changed to humorous ones as the Daily Mash usually does. Make the article no more than 200 words long. Include Markdown formatting for Jekyll. Below are some examples of Daily Mash style articles to give you an idea of the style.\n

Article Example:
# Man who can’t spell basic words demands you take his opinions seriously
Roy Hobbs thinks he is a serious commentator on issues of the day, despite using horrible misspellings like ‘probebly’, ‘interlectuals’ and ‘definately’.\n
Friend Emma Bradford said: “Roy hasn’t grasped that if he thinks ‘restoraunt’ is spelt like that people might realise he’s not an expert on politics, economics or any other subject.\n
“He’s constantly writing ‘looser’ when he means ‘loser’ and ‘lightening’ when he means ‘lightning’. When it comes to ‘there’, ‘their’ and ‘they’re’ I think he just picks one at random.\n
“He’s always spouting pompous reactionary crap, so a typical post will be, ‘In my estimatoin, a bridge with France would be disasterous. We do not want closure intergration with the Continant.’\n
Hobbs said: “Criticising someone’s spelling is a pathetic attempt to undermine valid arguments such as my view that we should ban transsexuals from TV to stop children thinking it’s ‘cool’.”\n

Article Example:
# Human beats highly advanced computer at drinking
In a move designed to test the limits of technology, 30-year-old roofer Wayne Hayes took on Google’s DeepMind machine in a pint-for-pint battle.\n
A Google spokesman said: “Having recently beaten the human champion at the board game Go, we were eager to test DeepMind at something that Westerners can understand and respect.”\n
The AI machine was fitted with a specially-adapted USB cable with a pint glass on one end into which beer could be poured. However it broke after two pints, exploding in a shower of sparks as Stella Artois flooded its motherboard.\n
Hayes said: “I was confident from the start because that computer just didn’t have the red, bulky look of a drinker about it.\n
“They can build these machines that can do all sums and everything, but they’ll never take over from man if they can’t handle 15-16 pints of export lager.”\n
However the Google spokesman added: “We should have added a ‘piss port’ to allow DeepMind to expel fluids. Also I think a little slot that you tip pork scratchings into would help.”\n

Original  News Headline: """


@stub.local_entrypoint()
def main():
    articles = get_news_articles(os.environ["GNEWS_API_KEY"], query, 1)
    stories = generate_post_respell.map(articles)

    # Write the stories to disk for local testing
    for story in stories:
        if story is not None:
            story.write_jekyll_file("_posts")


# Deploy to Modal and generate 3 articles per day
@stub.function(schedule=modal.Period(hours=8))
def scheduled():
    articles = get_news_articles(os.environ["GNEWS_API_KEY"], query, 1)
    stories = generate_post_respell.map(articles)

    # commit each post to GitHub
    for story in stories:
        if story is not None:
            commit_new_blog_post(story.jekyll_file_name(), story.jekyll_file_content())
            tweet_article(story)
