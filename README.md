# PR Reviewer AI

![Python](https://img.shields.io/badge/Python-3.x-blue?logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-API-green?logo=fastapi)
![Pytest](https://img.shields.io/badge/Pytest-Testing-orange?logo=pytest)
![GitHub Actions](https://img.shields.io/badge/GitHub-Actions-black?logo=githubactions)
![Gemini](https://img.shields.io/badge/Google-Gemini-blue)

AI-powered tool that automatically analyzes Pull Requests on GitHub and provides test suggestions and risk analysis for modified Python code.

The project combines static code analysis, Large Language Models, GitHub API integration, automated workflows, and developer feedback to assist the code review process from a QA perspective.

## Overview

PR Reviewer AI is designed to act as an additional layer of analysis during the Pull Request lifecycle.

When a Pull Request is opened or updated, GitHub Actions triggers the application, which analyzes the modified Python code and sends relevant functions to an LLM along with additional context.

The AI then generates suggestions such as:

* Potential test scenarios
* Risk identification
* Edge cases
* Validation opportunities
* Areas that may require additional testing

The generated suggestions are automatically posted as GitHub Pull Request review comments.

## How It Works

```text
Pull Request
     |
     v
GitHub Actions
     |
     v
FastAPI
     |
     v
GitHub API
     |
     v
Changed Python Files
     |
     v
AST Code Analysis
     |
     v
Context Gathering
     |
     v
LLM / Gemini
     |
     v
Test Suggestions
     |
     v
GitHub Review Comment
     |
     v
Developer Feedback
     |
     v
Database
     |
     v
Few-shot Examples
```

## Key Features

### Automated Pull Request Analysis

The GitHub Actions workflow automatically triggers the review when a Pull Request is:

* Opened
* Updated
* Reopened

The workflow sends the Pull Request information to the PR Reviewer API.

### Static Code Analysis

Modified Python files are analyzed using Python's AST module.

The analyzer extracts relevant functions from the changed files before sending them to the LLM.

This allows the application to focus the AI analysis on specific code changes instead of sending the entire repository.

### Context-Aware Analysis

The application gathers additional context before requesting an AI review.

The context can include:

* Modified functions
* Dependencies from other files
* Existing tests
* Pull Request changes

This provides the LLM with more information about how the modified code interacts with the rest of the project.

### AI-Generated Test Suggestions

The LLM analyzes the selected functions and generates testing suggestions based on the implementation and available context.

The goal is not to replace automated tests, but to help identify scenarios that may otherwise be overlooked during development.

### GitHub Review Comments

Suggestions are posted directly to the Pull Request.

Whenever possible, the application anchors the comment to the relevant changed line.

```text
Pull Request
      |
      +-- Modified function
      |
      +-- AI suggestion
      |
      +-- Review comment
      |
      +-- Developer response
```

If a suggestion cannot be associated with a specific changed line, the application uses a fallback comment at the end of the Pull Request.

### Developer Feedback

Developers can evaluate AI suggestions directly through Pull Request review comments.

Supported commands:

```text
/rate bom
/rate ruim
```

An optional reason can also be provided.

Example:

```text
/rate bom A validação sugerida cobre um cenário importante.
```

The webhook receives the response and associates the feedback with the original AI suggestion.

### Feedback-Based Improvement

Suggestions rated as good are stored in the database and can be reused as few-shot examples in future analyses.

```text
AI Suggestion
      |
      v
Developer Feedback
      |
      v
Database
      |
      v
Approved Examples
      |
      v
Future AI Analysis
```

This allows the system to improve the relevance of future suggestions without requiring model fine-tuning.

## Technologies

* Python
* FastAPI
* Pytest
* Google Gemini
* GitHub REST API
* GitHub Actions
* SQLModel
* SQLite
* PostgreSQL
* Pydantic
* HTTPX
* Python AST

## Project Structure

```text
pr-reviewer-ai/
│
├── app/
│   ├── main.py
│   ├── code_analyzer.py
│   ├── comment_formatter.py
│   ├── config.py
│   ├── context_gatherer.py
│   ├── db.py
│   ├── diff_utils.py
│   ├── github_client.py
│   ├── llm_client.py
│   └── models.py
│
├── tests/
│   ├── test_code_analyzer.py
│   ├── test_comment_formatter.py
│   ├── test_context_gatherer.py
│   ├── test_db.py
│   ├── test_diff_utils.py
│   ├── test_llm_client.py
│   └── test_main.py
│
├── .github/
│   └── workflows/
│       └── pr-review.yml
│
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

## Backend

The backend is built with FastAPI and is responsible for orchestrating the entire review process.

Main responsibilities:

* Receive Pull Request review requests
* Communicate with the GitHub API
* Retrieve modified files
* Analyze Python code using AST
* Gather repository context
* Send analysis requests to the LLM
* Format AI suggestions
* Create GitHub review comments
* Receive developer feedback through the webhook
* Store suggestions and feedback
* Retrieve approved examples for future analyses

## API Endpoints

### Review Pull Request

```http
POST /review
```

Example:

```json
{
  "owner": "github-user",
  "repo": "repository",
  "pr_number": 1,
  "head_ref": "commit-sha"
}
```

The endpoint starts the analysis process for the specified Pull Request.

### GitHub Webhook

```http
POST /webhook/github
```

The endpoint receives GitHub webhook events containing developer responses to AI review comments.

The feedback is used to associate ratings with previously generated suggestions.

## GitHub Actions

The project includes a GitHub Actions workflow responsible for triggering the review process.

```yaml
on:
  pull_request:
    types: [opened, synchronize, reopened]
```

When one of these events occurs, GitHub Actions sends a request to the deployed PR Reviewer API.

```text
Pull Request opened/updated
          |
          v
GitHub Actions
          |
          v
POST /review
          |
          v
PR Reviewer AI
```

## Environment Variables

Create a `.env` file based on `.env.example`.

The application requires configuration for GitHub authentication, the LLM provider, database connection, and webhook security.

Example:

```env
GITHUB_TOKEN=your_github_token
LLM_API_KEY=your_llm_api_key
DATABASE_URL=sqlite:///./reviewer.db
GITHUB_WEBHOOK_SECRET=your_webhook_secret
```

Do not commit API keys, tokens, secrets, or credentials to the repository.

## Setup

Clone the repository:

```bash
git clone https://github.com/reprateze/Pr-reviewer.git
cd Pr-reviewer
```

Create a virtual environment:

```bash
python -m venv venv
```

Activate the environment.

Linux/macOS:

```bash
source venv/bin/activate
```

Windows:

```bash
venv\Scripts\activate
```

Install the dependencies:

```bash
pip install -r requirements.txt
```

Configure the environment variables:

```bash
cp .env.example .env
```

Edit `.env` with the required credentials.

## Running the API

Start the FastAPI application:

```bash
uvicorn app.main:app --reload
```

The API will be available at:

```text
http://localhost:8000
```

FastAPI automatically provides interactive API documentation at:

```text
http://localhost:8000/docs
```

## Running Tests

Run the complete test suite:

```bash
pytest tests/ -v
```

The project includes automated tests covering the main application components:

* Code analysis
* Diff processing
* Context gathering
* Comment formatting
* Database operations
* LLM client
* API endpoints

## Testing Architecture

The automated tests are organized by application responsibility.

```text
tests/
    |
    +-- Code Analyzer
    |
    +-- Diff Utils
    |
    +-- Context Gatherer
    |
    +-- Comment Formatter
    |
    +-- Database
    |
    +-- LLM Client
    |
    +-- API
```

This structure allows individual components to be validated independently.

## GitHub Webhook Configuration

To enable developer feedback, configure a webhook in the repository where the PR Reviewer operates.

Go to:

```text
Repository
  → Settings
  → Webhooks
  → Add webhook
```

Configure:

```text
Payload URL:
<API_URL>/webhook/github

Content type:
application/json

Secret:
GITHUB_WEBHOOK_SECRET
```

Enable the Pull Request review comment event.

The webhook allows the application to receive commands such as:

```text
/rate bom
/rate ruim
```

## Database

The application uses SQLModel for database interaction.

SQLite can be used for local development:

```text
SQLite
```

PostgreSQL can be used in production:

```text
PostgreSQL
```

The database stores AI suggestions and developer feedback used by the feedback-based learning mechanism.

## Feedback and Few-shot Learning

One of the main ideas of the project is to use developer feedback to improve future AI reviews.

The process is:

```text
Code Change
    |
    v
AI Analysis
    |
    v
Suggestion
    |
    v
Developer Evaluation
    |
    +---- /rate bom ----> Stored as approved example
    |
    +---- /rate ruim ---> Stored as negative feedback
                              |
                              v
                       Future Analysis
```

Approved suggestions can be included as few-shot examples in future prompts.

This approach provides a lightweight feedback mechanism without requiring model fine-tuning.

## Fallback Strategy

The application attempts to anchor review comments directly to changed lines.

If the line cannot be identified or GitHub rejects the review comment, the system falls back to a general Pull Request comment.

This prevents the review process from losing the generated suggestion because of an anchoring problem.

## Current Status

### Completed

* [x] GitHub API integration
* [x] Pull Request analysis
* [x] Python AST analysis
* [x] Changed function extraction
* [x] Dependency context gathering
* [x] Existing test context
* [x] AI-powered test suggestions
* [x] GitHub review comments
* [x] Line-based comment anchoring
* [x] Fallback comments
* [x] Developer feedback through webhook
* [x] Suggestion persistence
* [x] Few-shot feedback mechanism
* [x] Automated tests

### Planned

* [ ] Production PostgreSQL deployment
* [ ] Complete webhook configuration
* [ ] End-to-end validation in a real repository
* [ ] Comparison between AI suggestions and human-written tests
* [ ] TCC methodology and results
* [ ] Support for additional programming languages
* [ ] Review history dashboard
* [ ] Suggestion priority classification
* [ ] Database migrations with Alembic

## Future Improvements

Possible future improvements include:

* Support for languages beyond Python
* Risk and priority classification
* Review history dashboard
* Improved test scenario generation
* Additional repository context
* Database migrations
* Improved production deployment
* Metrics for measuring AI suggestion quality
* Analysis of developer acceptance rate

## Purpose

This project explores the use of Artificial Intelligence to support software quality and the Pull Request review process.

The main goal is to combine QA practices with AI-assisted code analysis to identify potential test scenarios and risks before code reaches production.

The project demonstrates practical experience with:

* API development
* Automated testing
* QA automation
* Static code analysis
* REST APIs
* GitHub API
* GitHub Actions
* Artificial Intelligence
* LLM integration
* Test scenario generation
* Webhooks
* Database persistence
* Python

## Author

**Renan**

Junior QA / Software Quality Analyst focused on software testing, API testing, automation, and AI-assisted quality engineering.
