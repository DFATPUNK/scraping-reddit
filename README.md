# Scraping Reddit

## Overview
#### Simple script to scrap relevant posts in a Reddit thread.

Use heuristics to detect keywords; only accepts posts featuring numeric data (or numbers written in full letters). \
Feel free to change the heuristics to fine-tune your results.

Scores are calculated on the sum of heuristics that have worked on each post: **the more heuristics your post is eligible to, the higher its score is**.

## Usage
```
# Default
python reddit_thread_scraper.py "<URL>"

# Adjust minimum score
python reddit_thread_scraper.py "<URL>" --min_score 5

# Allow to extract posts that don't contain numerical value
python reddit_thread_scraper.py "<URL>" --allow_no_numbers
```

## Output
**Two files** in formats `.csv` and `.md`

Check our two examples of outputs from [this Reddit thread](https://www.reddit.com/r/AI_Agents/comments/1l3rmp6/anyone_here_actually_making_money_selling_ai/) in the repo:
* [`reddit_thread.csv`](https://github.com/DFATPUNK/scraping-reddit/blob/main/reddit_thread.csv)
* [`reddit_thread.md`](https://github.com/DFATPUNK/scraping-reddit/blob/main/reddit_thread.md)

## Contact
Email: [jeremy@jeremybrunet.com](mailto:jeremy@jeremybrunet.com)