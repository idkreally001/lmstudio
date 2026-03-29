# A lightweight Linux environment with Python and Git
FROM python:3.10-slim

# Install basic research tools and system dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    wget \
    gcc \
    g++ \
    make \
    python3-dev \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Pre-install Python libraries the AI might need for research
RUN pip install --no-cache-dir \
    requests \
    numpy \
    pandas \
    matplotlib \
    flask \
    beautifulsoup4 \
    psycopg2-binary

# Create non-root user for security
RUN groupadd -g 1000 sandbox && \
    useradd -u 1000 -g 1000 -m -s /bin/bash sandbox

# Set a working directory and give ownership to non-root user
WORKDIR /workspace
RUN chown -R sandbox:sandbox /workspace

# Switch to non-root user
USER 1000:1000

# Keep the container alive
CMD ["tail", "-f", "/dev/null"]