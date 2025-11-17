"""
E-commerce Product Image Scraper

A robust web scraper that extracts product images from e-commerce websites,
organizes them by collection, and optionally uploads them to Cloudinary.

Features:
    - Scrapes product images from specified websites
    - Organizes images by collection name extracted from URLs
    - Multi-threaded processing for faster downloads
    - Caches processed products and images in SQLite database
    - Optional Cloudinary integration for cloud storage
    - Handles errors gracefully with comprehensive logging
"""

import os
import re
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional, Tuple, Dict, List
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
import requests

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("Warning: python-dotenv not installed. "
          "Install with: pip install python-dotenv")
    print("Environment variables will be read from system only.")

# Cloudinary import (optional - will work even if not configured)
try:
    import cloudinary
    import cloudinary.uploader
    CLOUDINARY_AVAILABLE = True
except ImportError:
    CLOUDINARY_AVAILABLE = False
    print("Warning: Cloudinary not installed. "
          "Install with: pip install cloudinary")

# Configuration constants
BASE_URL = "https://www.joyfball.info"
SAVE_DIR = "images_folder"
DB_PATH = "scraper_cache.db"
REQUEST_TIMEOUT = 10
MAX_WORKERS = 5  # Number of concurrent threads for downloading

# Thread-safe database lock
db_lock = threading.Lock()

# Create save directory if it doesn't exist
if not os.path.exists(SAVE_DIR):
    os.makedirs(SAVE_DIR)

# HTTP headers to mimic a browser
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/91.0.4472.124 Safari/537.36"
    )
}

# Cloudinary configuration
if CLOUDINARY_AVAILABLE:
    cloudinary_url = os.getenv("CLOUDINARY_URL")
    if cloudinary_url:
        cloudinary.config(cloudinary_url=cloudinary_url)
    else:
        cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME", "")
        api_key = os.getenv("CLOUDINARY_API_KEY", "")
        api_secret = os.getenv("CLOUDINARY_API_SECRET", "")
        if cloud_name and api_key and api_secret:
            cloudinary.config(
                cloud_name=cloud_name,
                api_key=api_key,
                api_secret=api_secret
            )


def extract_image_from_page(url: str) -> Optional[str]:
    """
    Extract .jpg image URL from img.fantaskycdn.com from a product page.

    Args:
        url: The product page URL to scrape

    Returns:
        The image URL if found, None otherwise
    """
    try:
        response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        html_content = response.text

        if "img.fantaskycdn.com" in html_content:
            # Extract the URL using regex - specifically looking for .jpg files
            pattern = (
                r'https?://[^"\s]*img\.fantaskycdn\.com[^"\s]*\.jpg'
                r'(?:\?[^"\s]*)?'
            )
            matches = re.findall(pattern, html_content)
            if matches:
                return matches[0]

            # Try without https
            pattern = (
                r'//[^"\s]*img\.fantaskycdn\.com[^"\s]*\.jpg'
                r'(?:\?[^"\s]*)?'
            )
            matches = re.findall(pattern, html_content)
            if matches:
                return "https:" + matches[0]

        # Also check img tags
        html_file = BeautifulSoup(response.content, "html.parser")
        img_tags = html_file.find_all("img")

        for img_tag in img_tags:
            attrs = ["src", "data-src", "data-lazy-src",
                     "data-original", "data-srcset"]
            for attr in attrs:
                src = img_tag.get(attr)
                if (src and "img.fantaskycdn.com" in src and
                        re.search(r'\.jpg(?:\?|$|"|\s)', src)):
                    if src.startswith("//"):
                        return "https:" + src
                    return src

        return None
    except requests.RequestException as e:
        print(f"Error extracting image from {url}: {e}")
        return None
    except Exception as e:
        print(f"Unexpected error extracting image from {url}: {e}")
        return None


def extract_collection_name(product_url: str) -> str:
    """
    Extract collection name from product URL endpoint.

    Args:
        product_url: The product URL to extract collection name from

    Returns:
        The extracted collection name or "uncategorized" if extraction fails
    """
    try:
        parsed = urlparse(product_url)
        path_parts = parsed.path.strip("/").split("/")

        if len(path_parts) < 2 or path_parts[0] != "products":
            return "uncategorized"

        product_slug = path_parts[-1]
        words = product_slug.split("-")

        # Common team names to identify
        team_names = [
            "barcelona", "liverpool", "real-madrid", "madrid",
            "manchester", "chelsea", "arsenal"
        ]

        # Find meaningful words, stopping before random IDs/hashes
        meaningful_words = []
        for i, word in enumerate(words):
            # Stop if we hit a long random alphanumeric string
            if len(word) > 12 and word.isalnum():
                break
            # Stop if we hit very short random codes after meaningful content
            if (len(meaningful_words) >= 4 and len(word) <= 5 and
                    word.isalnum() and not word.isdigit()):
                # Check if it looks like a random code
                if any(c.isdigit() for c in word) and any(
                        c.isalpha() for c in word):
                    break

            meaningful_words.append(word)

        # Try to extract a meaningful collection name
        collection_words = []

        # Check if we have a year pattern at the start
        if (len(meaningful_words) >= 2 and
                meaningful_words[0].isdigit() and
                meaningful_words[1].isdigit()):
            collection_words.append(meaningful_words[0])
            collection_words.append(meaningful_words[1])
            start_idx = 2
        else:
            start_idx = 0

        # Look for team names
        team_found = False
        for i in range(start_idx, len(meaningful_words)):
            word = meaningful_words[i].lower()
            if word in team_names:
                collection_words.append(meaningful_words[i])
                team_found = True
                # Include next few words if they're part of product type
                if i + 1 < len(meaningful_words):
                    next_word = meaningful_words[i + 1].lower()
                    if next_word in ["home", "away", "third"]:
                        collection_words.append(meaningful_words[i + 1])
                        if (i + 2 < len(meaningful_words) and
                                meaningful_words[i + 2].lower() == "football"):
                            collection_words.append(meaningful_words[i + 2])
                break

        # If no team found, look for product type patterns
        if not team_found:
            # First, try to find multi-word product types
            max_idx = min(start_idx + 4, len(meaningful_words) - 1)
            for i in range(start_idx, max_idx):
                word = meaningful_words[i].lower()
                next_word = (meaningful_words[i + 1].lower()
                             if i + 1 < len(meaningful_words) else "")

                # Check for compound product types
                if word == "football" and next_word == "training":
                    collection_words.append(meaningful_words[i])
                    collection_words.append(meaningful_words[i + 1])
                    if (i + 2 < len(meaningful_words) and
                            meaningful_words[i + 2].lower() == "uniform"):
                        collection_words.append(meaningful_words[i + 2])
                    break
                elif word == "training" and next_word == "uniform":
                    if i > 0:
                        collection_words.append(meaningful_words[i - 1])
                    collection_words.append(meaningful_words[i])
                    collection_words.append(meaningful_words[i + 1])
                    break
                elif word in ["football", "soccer"] and next_word in [
                        "shirt", "jersey"]:
                    collection_words.append(meaningful_words[i])
                    collection_words.append(meaningful_words[i + 1])
                    break

            # If no compound pattern found, look for single product type words
            if not collection_words:
                max_idx = min(start_idx + 5, len(meaningful_words))
                for i in range(start_idx, max_idx):
                    word = meaningful_words[i].lower()
                    if word in ["football", "training", "uniform",
                                "shirt", "jersey", "soccer"]:
                        # Include preceding word if it's part of the type
                        if i > 0:
                            prev_word = meaningful_words[i - 1].lower()
                            if prev_word in ["football", "training"]:
                                collection_words.append(meaningful_words[i - 1])
                        collection_words.append(meaningful_words[i])
                        # Include following word if it completes the type
                        if i + 1 < len(meaningful_words):
                            next_word = meaningful_words[i + 1].lower()
                            if next_word in ["uniform", "shirt", "jersey"]:
                                collection_words.append(meaningful_words[i + 1])
                        break

        # Fallback: use first 3-4 meaningful words
        if not collection_words:
            if len(meaningful_words) >= 4:
                collection_words = meaningful_words[:4]
            else:
                collection_words = meaningful_words[:3]

        # Remove common suffixes that aren't part of collection name
        stop_words = [
            "1-1", "thai", "quality", "version", "player",
            "hobl", "4axi", "m86h", "fws4", "4xdq"
        ]
        filtered_words = []
        for word in collection_words:
            if word.lower() not in stop_words:
                filtered_words.append(word)
            else:
                break  # Stop at first stop word

        if not filtered_words:
            filtered_words = (collection_words[:3]
                              if collection_words else ["uncategorized"])

        collection_name = "-".join(filtered_words)
        # Clean up the collection name
        collection_name = re.sub(r'[^\w\-]', '_', collection_name)
        collection_name = collection_name.strip('-_')

        return collection_name if collection_name else "uncategorized"
    except Exception as e:
        print(f"Error extracting collection name from {product_url}: {e}")
        return "uncategorized"


def download_image(image_url: str, filename: str,
                   collection_dir: str) -> bool:
    """
    Download an image from URL to the collection directory.

    Args:
        image_url: The URL of the image to download
        filename: The filename to save the image as
        collection_dir: The directory to save the image in

    Returns:
        True if download successful, False otherwise
    """
    try:
        response = requests.get(
            image_url, headers=HEADERS, timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()

        # Ensure collection directory exists
        if not os.path.exists(collection_dir):
            os.makedirs(collection_dir, exist_ok=True)

        filepath = os.path.join(collection_dir, filename)
        with open(filepath, 'wb') as f:
            f.write(response.content)
        return True
    except requests.RequestException as e:
        print(f"Error downloading {image_url}: {e}")
        return False
    except IOError as e:
        print(f"Error writing file {filename}: {e}")
        return False
    except Exception as e:
        print(f"Unexpected error downloading {image_url}: {e}")
        return False


def upload_to_cloudinary(image_url: str, collection_name: str,
                         public_id: Optional[str] = None) -> Optional[Dict]:
    """
    Upload an image to Cloudinary from a URL.

    Args:
        image_url: The URL of the image to upload
        collection_name: The collection name for folder organization
        public_id: Optional public ID for the image

    Returns:
        Cloudinary upload result dict if successful, None otherwise
    """
    if not CLOUDINARY_AVAILABLE:
        return None

    try:
        # Use collection name as folder path in Cloudinary
        folder_path = f"products/{collection_name}"

        # Generate public_id from filename if not provided
        if not public_id:
            # Remove extension for public_id
            public_id = os.path.splitext(
                os.path.basename(urlparse(image_url).path)
            )[0]
            public_id = re.sub(r'[^\w\-]', '_', public_id)

        # Full public_id with folder
        full_public_id = f"{folder_path}/{public_id}"

        # Upload from URL directly to Cloudinary
        result = cloudinary.uploader.upload(
            image_url,
            public_id=full_public_id,
            folder=folder_path,
            resource_type="image",
            overwrite=True,
        )

        return result
    except Exception as e:
        print(f"Error uploading to Cloudinary: {e}")
        return None


def init_db() -> None:
    """Initialize SQLite database with tables for products and images."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Create products table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_url TEXT UNIQUE NOT NULL,
                collection_name TEXT,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                image_found BOOLEAN DEFAULT 0,
                last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Create images table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_url TEXT NOT NULL,
                image_url TEXT NOT NULL,
                collection_name TEXT,
                local_path TEXT,
                cloudinary_url TEXT,
                cloudinary_public_id TEXT,
                downloaded_at TIMESTAMP,
                uploaded_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(product_url, image_url)
            )
        ''')

        # Create indexes for faster lookups
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_products_url '
            'ON products(product_url)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_images_product_url '
            'ON images(product_url)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_images_image_url '
            'ON images(image_url)'
        )

        conn.commit()
        conn.close()
    except sqlite3.Error as e:
        print(f"Database initialization error: {e}")
        raise
    except Exception as e:
        print(f"Unexpected error initializing database: {e}")
        raise


def get_db_connection() -> sqlite3.Connection:
    """
    Get a database connection with thread-safe check_same_thread=False.

    Returns:
        SQLite database connection
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    # Enable WAL mode for better concurrent access
    conn.execute('PRAGMA journal_mode=WAL')
    return conn


def is_product_processed(product_url: str) -> bool:
    """
    Check if a product has already been processed (thread-safe).

    Args:
        product_url: The product URL to check

    Returns:
        True if product is already processed, False otherwise
    """
    try:
        with db_lock:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                'SELECT id FROM products WHERE product_url = ?',
                (product_url,)
            )
            result = cursor.fetchone()
            conn.close()
            return result is not None
    except sqlite3.Error as e:
        print(f"Database error checking product: {e}")
        return False
    except Exception as e:
        print(f"Unexpected error checking product: {e}")
        return False


def is_image_downloaded(image_url: str,
                        product_url: Optional[str] = None) -> Tuple[
                            bool, Optional[Dict]]:
    """
    Check if an image has already been downloaded/uploaded (thread-safe).

    Args:
        image_url: The image URL to check
        product_url: Optional product URL for more specific lookup

    Returns:
        Tuple of (exists, record_dict) where record_dict contains
        the cached record if exists
    """
    try:
        with db_lock:
            conn = get_db_connection()
            cursor = conn.cursor()

            if product_url:
                cursor.execute('''
                    SELECT local_path, cloudinary_url, downloaded_at, uploaded_at
                    FROM images
                    WHERE image_url = ? AND product_url = ?
                ''', (image_url, product_url))
            else:
                cursor.execute('''
                    SELECT local_path, cloudinary_url, downloaded_at, uploaded_at
                    FROM images
                    WHERE image_url = ?
                ''', (image_url,))

            result = cursor.fetchone()
            conn.close()

            if result:
                return True, {
                    'local_path': result[0],
                    'cloudinary_url': result[1],
                    'downloaded_at': result[2],
                    'uploaded_at': result[3]
                }
            return False, None
    except sqlite3.Error as e:
        print(f"Database error checking image: {e}")
        return False, None
    except Exception as e:
        print(f"Unexpected error checking image: {e}")
        return False, None


def save_product(product_url: str, collection_name: str,
                 image_found: bool = False) -> None:
    """
    Save or update a product record in the database (thread-safe).

    Args:
        product_url: The product URL
        collection_name: The collection name
        image_found: Whether an image was found for this product
    """
    try:
        with db_lock:
            conn = get_db_connection()
            cursor = conn.cursor()

            cursor.execute('''
                INSERT OR REPLACE INTO products
                (product_url, collection_name, image_found, last_checked)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ''', (product_url, collection_name, 1 if image_found else 0))

            conn.commit()
            conn.close()
    except sqlite3.Error as e:
        print(f"Database error saving product: {e}")
    except Exception as e:
        print(f"Unexpected error saving product: {e}")


def save_image(product_url: str, image_url: str, collection_name: str,
               local_path: Optional[str] = None,
               cloudinary_url: Optional[str] = None,
               cloudinary_public_id: Optional[str] = None,
               downloaded: bool = False,
               uploaded: bool = False) -> None:
    """
    Save or update an image record in the database (thread-safe).

    Args:
        product_url: The product URL
        image_url: The image URL
        collection_name: The collection name
        local_path: Local file path if downloaded
        cloudinary_url: Cloudinary URL if uploaded
        cloudinary_public_id: Cloudinary public ID if uploaded
        downloaded: Whether image was downloaded
        uploaded: Whether image was uploaded
    """
    try:
        with db_lock:
            conn = get_db_connection()
            cursor = conn.cursor()

            downloaded_at = datetime.now().isoformat() if downloaded else None
            uploaded_at = datetime.now().isoformat() if uploaded else None

            cursor.execute('''
                INSERT OR REPLACE INTO images
                (product_url, image_url, collection_name, local_path,
                 cloudinary_url, cloudinary_public_id, downloaded_at,
                 uploaded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (product_url, image_url, collection_name, local_path,
                  cloudinary_url, cloudinary_public_id, downloaded_at,
                  uploaded_at))

            conn.commit()
            conn.close()
    except sqlite3.Error as e:
        print(f"Database error saving image: {e}")
    except Exception as e:
        print(f"Unexpected error saving image: {e}")


def get_all_product_links(start_url: Optional[str] = None) -> List[str]:
    """
    Get all product links from the website.

    Args:
        start_url: Optional starting URL to begin crawling from

    Returns:
        List of unique product URLs
    """
    product_links = set()

    # Try to find product links from the homepage and collection pages
    pages_to_check = [BASE_URL]

    # Try to find category/collection pages from homepage
    try:
        response = requests.get(
            BASE_URL, headers=HEADERS, timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        # Look for category/collection links
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            if any(x in href for x in ["/collections/", "/categories/",
                                       "/category/"]):
                full_url = urljoin(BASE_URL, href)
                if full_url not in pages_to_check:
                    pages_to_check.append(full_url)
    except requests.RequestException:
        pass
    except Exception as e:
        print(f"Error finding collection pages: {e}")

    if start_url:
        pages_to_check.insert(0, start_url)

    visited_pages = set()
    i = 0
    while i < len(pages_to_check):
        page_url = pages_to_check[i]
        i += 1

        # Skip if already visited
        if page_url in visited_pages:
            continue
        visited_pages.add(page_url)

        try:
            print(f"Checking {page_url} for product links...")
            response = requests.get(
                page_url, headers=HEADERS, timeout=REQUEST_TIMEOUT
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.content, "html.parser")

            # Find all links that point to product pages
            for link in soup.find_all("a", href=True):
                href = link.get("href")
                if href and "/products/" in href:
                    # Skip template variables and invalid links
                    if "${" in href or "{" in href or "}" in href:
                        continue
                    # Convert relative URLs to absolute
                    full_url = urljoin(BASE_URL, href)
                    # Remove query parameters and fragments
                    parsed = urlparse(full_url)
                    clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                    # Skip if path is just /products/ without a product name
                    if clean_url.rstrip("/").endswith("/products"):
                        continue
                    product_links.add(clean_url)

            # Check for pagination links
            for link in soup.find_all("a", href=True):
                href = link.get("href", "")
                text = link.get_text(strip=True).lower()
                # Look for pagination indicators
                pagination_indicators = ["next", "page", "more", "load more"]
                pagination_params = ["page=", "p=", "offset="]
                if (any(indicator in text for indicator in pagination_indicators)
                        or any(param in href.lower()
                                for param in pagination_params)):
                    full_url = urljoin(BASE_URL, href)
                    if (full_url not in visited_pages and
                            full_url not in pages_to_check):
                        pages_to_check.append(full_url)

            print(f"Found {len(product_links)} unique product links so far...")
        except requests.RequestException as e:
            print(f"Error checking {page_url}: {e}")
            continue
        except Exception as e:
            print(f"Unexpected error checking {page_url}: {e}")
            continue

    return list(product_links)


def main() -> None:
    """Main function to orchestrate the scraping process."""
    print("Starting image extraction from all products...")

    # Initialize database
    print("\nInitializing database cache...")
    try:
        init_db()
        print(f"Database initialized: {DB_PATH}")
    except Exception as e:
        print(f"Failed to initialize database: {e}")
        print("Exiting...")
        return

    # Get all product links
    print("\nStep 1: Finding all product links...")
    product_links = get_all_product_links()

    if not product_links:
        print("No product links found. Trying with the provided link...")
        # Fallback: use the provided link as a starting point
        provided_link = (
            "https://www.joyfball.info/products/"
            "football-training-uniform-fhvs-gioi-805p-38zw-it08-6iog-4qvu-"
            "fqds-fet8-xeol-fwmc-f5oi-aeec-wwcj-zpa4-t39m-frcl-p05t-owhx-"
            "d01m-5npn-y8n9-j8t6-9yve-7xjk-2zmr-4ur0-824a-25ax-cf1f-169k-"
            "7ywv-285m-fmsc-4582-ixbh-8t4b-a90m-7obx-yab5-fbmj-0u38-4rtf-"
            "cyx7-yhmt-wyu8"
        )
        product_links = get_all_product_links(provided_link)

    print(f"\nFound {len(product_links)} product pages to process")

    # Extract images from each product
    print("\nStep 2: Extracting images from each product page...")
    print(f"Using {MAX_WORKERS} worker threads for parallel processing...")

    # Check Cloudinary configuration
    use_cloudinary = False
    if CLOUDINARY_AVAILABLE:
        try:
            # Check if Cloudinary is configured
            config = cloudinary.config()
            # Check if cloud_name is set (indicates configuration)
            if hasattr(config, 'cloud_name') and config.cloud_name:
                use_cloudinary = True
                print("Cloudinary configured - "
                      "images will be uploaded to Cloudinary")
            else:
                print("Cloudinary not configured - set CLOUDINARY_URL or "
                      "CLOUDINARY_CLOUD_NAME, API_KEY, API_SECRET")
                print("  Example: export CLOUDINARY_URL="
                      "'cloudinary://api_key:api_secret@cloud_name'")
        except Exception as e:
            print(f"Cloudinary configuration error: {e}")
            print("Continuing without Cloudinary upload...")

    # Thread-safe counters
    stats_lock = threading.Lock()
    downloaded_count = 0
    uploaded_count = 0
    failed_count = 0
    processed_count = 0

    def process_product(product_url: str) -> Dict[str, int]:
        """
        Process a single product (worker function for threading).

        Args:
            product_url: The product URL to process

        Returns:
            Dictionary with statistics for this product
        """
        result = {
            'downloaded': 0,
            'uploaded': 0,
            'failed': 0
        }

        try:
            # Check if product already processed
            if is_product_processed(product_url):
                return result

            # Extract collection name from URL
            collection_name = extract_collection_name(product_url)
            collection_dir = os.path.join(SAVE_DIR, collection_name)

            image_url = extract_image_from_page(product_url)

            if image_url:
                # Check if image already downloaded/uploaded
                image_cached, cache_record = is_image_downloaded(
                    image_url, product_url
                )

                if image_cached:
                    local_success = False
                    if (cache_record and cache_record.get('local_path') and
                            os.path.exists(cache_record['local_path'])):
                        local_success = True
                        result['downloaded'] = 1

                    if cache_record and cache_record.get('cloudinary_url'):
                        if use_cloudinary:
                            result['uploaded'] = 1

                    # Update product record
                    save_product(product_url, collection_name,
                                image_found=True)

                    if local_success or (cache_record and
                                         cache_record.get('cloudinary_url')):
                        return result
                    # If cached but files missing, re-download

                # Generate filename from product URL or image URL
                product_slug = (urlparse(product_url).path.split("/")[-1]
                                or "product")
                image_filename = (os.path.basename(urlparse(image_url).path)
                                  or f"{product_slug}.jpg")

                # Ensure .jpg extension
                if not image_filename.endswith(".jpg"):
                    image_filename = f"{product_slug}.jpg"

                # Make filename safe
                image_filename = re.sub(r'[^\w\-_\.]', '_', image_filename)

                # Download image locally
                filepath = os.path.join(collection_dir, image_filename)
                local_success = False
                if os.path.exists(filepath):
                    local_success = True
                    result['downloaded'] = 1
                else:
                    if download_image(image_url, image_filename,
                                     collection_dir):
                        local_success = True
                        result['downloaded'] = 1

                # Upload to Cloudinary if configured
                cloudinary_url = None
                cloudinary_public_id = None
                if use_cloudinary:
                    # Check if already uploaded (from cache)
                    if (cache_record and
                            cache_record.get('cloudinary_url')):
                        cloudinary_url = cache_record['cloudinary_url']
                        result['uploaded'] = 1
                    else:
                        cloudinary_result = upload_to_cloudinary(
                            image_url, collection_name
                        )
                        if cloudinary_result:
                            cloudinary_url = cloudinary_result.get(
                                'secure_url',
                                cloudinary_result.get('url', '')
                            )
                            cloudinary_public_id = cloudinary_result.get(
                                'public_id', ''
                            )
                            result['uploaded'] = 1

                # Save to database
                save_product(product_url, collection_name, image_found=True)
                save_image(
                    product_url=product_url,
                    image_url=image_url,
                    collection_name=collection_name,
                    local_path=filepath if local_success else None,
                    cloudinary_url=cloudinary_url,
                    cloudinary_public_id=cloudinary_public_id,
                    downloaded=local_success,
                    uploaded=bool(cloudinary_url)
                )

                if not local_success and not cloudinary_url:
                    result['failed'] = 1
            else:
                save_product(product_url, collection_name, image_found=False)
                result['failed'] = 1
        except Exception as e:
            print(f"Error processing {product_url}: {e}")
            result['failed'] = 1

        return result

    # Process products in parallel using ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # Submit all tasks
        future_to_url = {
            executor.submit(process_product, url): url
            for url in product_links
        }

        # Process completed tasks
        for future in as_completed(future_to_url):
            product_url = future_to_url[future]
            try:
                stats = future.result()
                with stats_lock:
                    downloaded_count += stats['downloaded']
                    uploaded_count += stats['uploaded']
                    failed_count += stats['failed']
                    processed_count += 1
                    print(f"[{processed_count}/{len(product_links)}] "
                          f"Processed: {product_url[:60]}...")
            except Exception as e:
                with stats_lock:
                    failed_count += 1
                    processed_count += 1
                print(f"Error processing {product_url}: {e}")

    print(f"\n{'='*60}")
    print("Extraction complete!")
    print(f"Total products processed: {len(product_links)}")
    print(f"Successfully downloaded locally: {downloaded_count}")
    if use_cloudinary:
        print(f"Successfully uploaded to Cloudinary: {uploaded_count}")
    print(f"Failed/Missing: {failed_count}")
    print(f"Images saved to: {os.path.abspath(SAVE_DIR)}")

    # Show database statistics
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM products')
        total_products = cursor.fetchone()[0]
        cursor.execute('SELECT COUNT(*) FROM images')
        total_images = cursor.fetchone()[0]
        cursor.execute(
            'SELECT COUNT(*) FROM images WHERE downloaded_at IS NOT NULL'
        )
        downloaded_images = cursor.fetchone()[0]
        cursor.execute(
            'SELECT COUNT(*) FROM images WHERE uploaded_at IS NOT NULL'
        )
        uploaded_images = cursor.fetchone()[0]
        conn.close()

        print("\nDatabase Statistics:")
        print(f"  Total products in cache: {total_products}")
        print(f"  Total images in cache: {total_images}")
        print(f"  Images downloaded: {downloaded_images}")
        if use_cloudinary:
            print(f"  Images uploaded to Cloudinary: {uploaded_images}")
    except Exception as e:
        print(f"Error retrieving database statistics: {e}")

    # Show collection statistics
    if os.path.exists(SAVE_DIR):
        try:
            collections = [
                d for d in os.listdir(SAVE_DIR)
                if os.path.isdir(os.path.join(SAVE_DIR, d))
            ]
            if collections:
                print(f"\nCollections created: {len(collections)}")
                for collection in sorted(collections):
                    collection_path = os.path.join(SAVE_DIR, collection)
                    image_count = len([
                        f for f in os.listdir(collection_path)
                        if f.endswith('.jpg')
                    ])
                    print(f"  - {collection}: {image_count} image(s)")
        except OSError as e:
            print(f"Error reading collections directory: {e}")


if __name__ == "__main__":
    main()
