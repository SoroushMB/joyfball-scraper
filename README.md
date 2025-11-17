# E-commerce Product Image Scraper

A robust, production-ready Python web scraper that extracts product images from e-commerce websites, organizes them by collection, and optionally uploads them to Cloudinary.

## Features

- 🖼️ **Image Extraction**: Automatically extracts `.jpg` product images from e-commerce websites
- 📁 **Collection Organization**: Intelligently categorizes images by collection name extracted from URLs
- ⚡ **Multi-threaded Processing**: Parallel downloads using multiple worker threads (configurable, default: 5)
- 💾 **SQLite Caching**: Caches processed products and images to avoid re-processing
- ☁️ **Cloudinary Integration**: Optional cloud storage upload with automatic organization
- 🔄 **Resume Capability**: Can stop and resume without re-processing already downloaded images
- 🛡️ **Error Handling**: Comprehensive error handling with graceful degradation
- 📊 **Statistics**: Detailed statistics on processed products and images
- 🔒 **Thread-safe**: All database operations are thread-safe with proper locking

## Requirements

- Python 3.12+
- pip or uv package manager

## Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd scrapping-images
```

2. Install dependencies:
```bash
# Using pip
pip install -r requirements.txt

# Or using uv (recommended)
uv sync
```

## Configuration

### Environment Variables

Create a `.env` file in the project root (see `.env.example`):

```bash
# Option 1: Use CLOUDINARY_URL (recommended)
CLOUDINARY_URL=cloudinary://api_key:api_secret@cloud_name

# Option 2: Use individual variables
CLOUDINARY_CLOUD_NAME=your_cloud_name
CLOUDINARY_API_KEY=your_api_key
CLOUDINARY_API_SECRET=your_api_secret
```

Get your Cloudinary credentials from [Cloudinary Dashboard](https://console.cloudinary.com/).

**Note**: Cloudinary is optional. The scraper works fine without it, storing images locally only.

## Usage

Run the scraper:

```bash
python main.py
```

The script will:
1. Initialize the SQLite database cache
2. Find all product links from the website
3. Extract images from each product page
4. Organize images into collection folders
5. Optionally upload to Cloudinary (if configured)
6. Display comprehensive statistics

## Project Structure

```
scrapping-images/
├── main.py              # Main scraper script
├── pyproject.toml       # Project dependencies
├── .env                 # Environment variables (not in git)
├── .env.example         # Environment variables template
├── .gitignore          # Git ignore rules
├── scraper_cache.db     # SQLite database cache (not in git)
├── images_folder/       # Downloaded images organized by collection
└── README.md           # This file
```

## Database Schema

The scraper uses SQLite to cache processed data:

### Products Table
- `product_url`: Unique product URL
- `collection_name`: Extracted collection name
- `processed_at`: Timestamp of processing
- `image_found`: Boolean indicating if image was found
- `last_checked`: Last check timestamp

### Images Table
- `product_url`: Associated product URL
- `image_url`: Source image URL
- `collection_name`: Collection name
- `local_path`: Local file path (if downloaded)
- `cloudinary_url`: Cloudinary URL (if uploaded)
- `cloudinary_public_id`: Cloudinary public ID
- `downloaded_at`: Download timestamp
- `uploaded_at`: Upload timestamp

## Collection Name Extraction

The scraper intelligently extracts collection names from product URLs by:
- Identifying team names (Barcelona, Liverpool, etc.)
- Recognizing product types (football-shirt, training-uniform, etc.)
- Handling year patterns (2025-2026)
- Filtering out random IDs and hash codes

Examples:
- `/products/football-training-uniform-...` → `football-training-uniform`
- `/products/2025-2026-barcelona-home-football-shirt-...` → `2025-2026-barcelona-home-football`
- `/products/2024-2025-liverpool-third-away-soccer-jersey-...` → `2024-2025-liverpool-third`

## Error Handling

The scraper includes comprehensive error handling:
- Network errors (timeouts, connection failures)
- File system errors (permissions, disk space)
- Database errors (corruption, locks)
- Cloudinary API errors
- HTML parsing errors

All errors are logged with descriptive messages, and the scraper continues processing other products.

## Performance

- **Multi-threading**: Processes multiple products in parallel (default: 5 concurrent threads)
- **Caching**: Already processed products are skipped automatically
- **Database Indexing**: Fast lookups with indexed database queries with WAL mode for concurrent access
- **Resume Capability**: Can stop and resume without losing progress
- **Thread-safe**: All database operations use proper locking mechanisms

### Adjusting Thread Count

You can modify the `MAX_WORKERS` constant in `main.py` to adjust the number of concurrent threads:
- Lower values (2-3): More conservative, less server load
- Default (5): Balanced performance
- Higher values (10+): Faster but may overwhelm the server (use responsibly)

## Contributing

Contributions are welcome! Please ensure:
- Code follows PEP 8 style guidelines
- All functions have proper docstrings
- Error handling is comprehensive
- Type hints are included where appropriate

## License

See LICENSE file for details.

## Disclaimer

This scraper is for educational and personal use only. Always respect:
- Website terms of service
- Robots.txt files
- Rate limiting guidelines
- Copyright laws

Use responsibly and ethically.

