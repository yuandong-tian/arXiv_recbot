"""
Core functionality for arXiv paper fetching, rating, and database operations.
This module is platform-agnostic and can be used by both Telegram and Slack bots.
"""
import os
import sqlite3
import torch
import joblib
import threading
from datetime import datetime, timedelta, timezone
from typing import List, Tuple, Optional, Callable, Any
from arxiv_util import get_arxiv_results, get_arxiv_message
from preference_model import PreferenceModel
from common import global_model_name, global_vectorizer_name, global_dataset_name

# Constants
MAX_RESULTS = 100

# Global model and vectorizer
loaded_model = None
vectorizer = None

# Thread-local database connections
_thread_local = threading.local()
_db_lock = threading.Lock()


def initialize_model():
    """Initialize the preference model and vectorizer if they exist."""
    global loaded_model, vectorizer
    
    if os.path.exists(global_model_name):
        vectorizer = joblib.load(global_vectorizer_name)
        loaded_model = PreferenceModel(vectorizer.get_feature_names_out().shape[0], 6)
        loaded_model.load_state_dict(torch.load(global_model_name))
        loaded_model.eval()
        print(f"Loaded {global_model_name} and {global_vectorizer_name}")
    else:
        loaded_model = None
        vectorizer = None


def get_db_connection():
    """Get a thread-local database connection."""
    if not hasattr(_thread_local, 'conn') or _thread_local.conn is None:
        _thread_local.conn = sqlite3.connect(global_dataset_name, check_same_thread=False)
        _thread_local.cursor = _thread_local.conn.cursor()
        
        # Create tables if they don't exist
        # Updated to use paper_id (entry_id) instead of paper_message_id
        with _db_lock:
            _thread_local.cursor.execute('CREATE TABLE IF NOT EXISTS infos (id INTEGER PRIMARY KEY, paper_id TEXT, text TEXT)')
            _thread_local.cursor.execute('CREATE TABLE IF NOT EXISTS comments (id INTEGER PRIMARY KEY, message_id INTEGER, paper_id TEXT, comment TEXT)')
            # Updated preferences table: paper_id (entry_id), person_id (user_id), timestamp, preference
            _thread_local.cursor.execute('CREATE TABLE IF NOT EXISTS preferences (id INTEGER PRIMARY KEY, paper_id TEXT, person_id TEXT, timestamp TEXT, preference INTEGER)')
            _thread_local.conn.commit()
    
    return _thread_local.conn, _thread_local.cursor


def initialize_database():
    """Initialize the database connection and create tables if they don't exist."""
    # Just ensure tables exist by getting a connection
    get_db_connection()


def get_rated_papers(keywords: str, backdays: int) -> List[Tuple[float, str, str]]:
    """
    Fetch papers from arXiv and rate them using the preference model.
    
    Args:
        keywords: Comma-separated keywords for arXiv search
        backdays: Number of days to look back for papers
        
    Returns:
        List of tuples (overall_rating, message, entry_id) sorted by rating
    """
    results = get_arxiv_results(keywords.replace(",", " OR "), MAX_RESULTS)
    
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=backdays)
    
    papers_to_send = []
    
    for result in results:
        submitted_date = result.updated
        # Make the submitted date timezone-aware by assuming UTC
        submitted_date = submitted_date.replace(tzinfo=timezone.utc)
        if submitted_date >= yesterday:
            message = get_arxiv_message(result)
            
            if loaded_model and vectorizer:
                # Predict the class of the paper
                X = vectorizer.transform([message])
                X_tensor = torch.tensor(X.toarray(), dtype=torch.float32)
                prediction = loaded_model(X_tensor)
                # Prepend predicted probabilities of all classes to output text
                y_pred_proba = prediction.softmax(dim=1).detach().cpu()
                y_pred_proba = y_pred_proba[0]
                
                # Compute an overall rating for the paper.
                # The rating is a weighted sum of the predicted probabilities of all classes.
                overall_rating = torch.dot(y_pred_proba, torch.arange(y_pred_proba.shape[0]).float()).item()
                
                message = f"// {overall_rating} {y_pred_proba}\n{message}"
            else:
                # No model to load yet
                overall_rating = 0
                message = f"// no model yet\n{message}"
            
            papers_to_send.append((overall_rating, message, result.entry_id))
    
    # Sort papers_to_send by overall_rating in descending order
    papers_to_send.sort(key=lambda x: x[0], reverse=True)
    # Select the top 10 papers
    papers_to_send = papers_to_send[:10]
    
    return papers_to_send


def retrieve_papers_by_tag(tag: str) -> List[str]:
    """
    Retrieve papers from the database that contain the given tag.
    
    Args:
        tag: Tag to search for in comments
        
    Returns:
        List of paper text strings
    """
    conn, cursor = get_db_connection()
    
    # Retrieve the paper from the database that contains the tags
    cursor.execute('SELECT paper_id FROM comments WHERE comment LIKE ?', ('%' + tag + '%',))
    paper_ids = cursor.fetchall()
    
    # Get all papers that contain the tags and return
    papers = []
    
    for paper_id_tuple in paper_ids:
        paper_id = paper_id_tuple[0]
        # Retrieve the paper from the database using paper_id (entry_id)
        cursor.execute('SELECT text FROM infos WHERE paper_id = ?', (paper_id,))
        for paper in cursor.fetchall():
            # Convert the paper to a string
            papers.append(str(paper[0]))
    
    return papers


def save_paper_info(entry_id: str, paper_text: str):
    """
    Save paper information to the database.
    
    Args:
        entry_id: arXiv entry ID (paper_id)
        paper_text: Text content of the paper (without rating prefix)
    """
    conn, cursor = get_db_connection()
    
    # Remove the rating prefix if present (lines starting with "//")
    clean_text = paper_text
    if paper_text.startswith("//"):
        # Remove the first line which contains the rating
        lines = paper_text.split("\n")
        if len(lines) > 1:
            clean_text = "\n".join(lines[1:])
    
    # Check if paper already exists using paper_id (entry_id)
    cursor.execute('SELECT id FROM infos WHERE paper_id = ?', (entry_id,))
    if cursor.fetchone() is None:
        cursor.execute('INSERT INTO infos (paper_id, text) VALUES (?, ?)', (entry_id, clean_text))
        conn.commit()
        import logging
        logging.info(f"Saved paper info: paper_id={entry_id}")


def save_feedback(feedback_type: str, entry_id: str, user_id: str):
    """
    Save user feedback to the database.
    
    Args:
        feedback_type: Type of feedback (e.g., "rating1", "rating2", etc.)
        entry_id: arXiv entry ID (paper_id) - used to match feedback with papers
        user_id: User ID who provided the feedback (person_id)
    """
    conn, cursor = get_db_connection()
    
    # Map feedback_type to preference integer
    # rating1 -> 0, rating2 -> 1, ..., rating6 -> 5
    label2class = {f"rating{i+1}": i for i in range(6)}
    label2class.update({
        "not": 0,
        "thumb": 4,
        "love": 5,
    })
    
    preference = label2class.get(feedback_type, None)
    
    if preference is None:
        import logging
        logging.warning(f"Unknown feedback type: {feedback_type}")
        return
    
    # Get current timestamp
    timestamp = datetime.now(timezone.utc).isoformat()
    
    # Save preference to database with paper_id (entry_id), person_id (user_id), timestamp, and preference
    cursor.execute(
        'INSERT INTO preferences (paper_id, person_id, timestamp, preference) VALUES (?, ?, ?, ?)',
        (entry_id, user_id, timestamp, preference)
    )
    conn.commit()
    
    import logging
    logging.info(f"Saved feedback: feedback_type={feedback_type}, preference={preference}, paper_id={entry_id}, person_id={user_id}, timestamp={timestamp}")


# Initialize on import
initialize_model()
initialize_database()

