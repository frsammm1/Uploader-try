import os
import shutil
from threading import Thread
from flask import Flask

# Create Flask app for health check (required by Render)
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!", 200

@app.route('/health')
def health():
    return "OK", 200

def run_flask():
    """Run Flask server"""
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def main():
    #@title <h1><B><font color=red>𝗥𝗲𝗽𝗼 𝗧𝗲𝘀𝘁 𝗛𝗼𝘄 𝗧𝗼 𝗚𝗼𝗼𝗴𝗹𝗲  <img src='https://i.ibb.co/ZLbRGmT/Picsart-24-02-16-14-30-48-873.png' height="40" /> </center> { display-mode: "form" }
    # @markdown <div><center><a href="https://github.com/terabox25/Repo-Test-Colab/graphs/contributors"><img height="200"  src="https://opengraph.githubassets.com/niszjzjrdlws31z4hurrzabavate8t0g/terabox25/Repo-Test-Colab"></center></div>
    # @markdown <br><center><h2><strong><font color=red>🔗 𝗥𝗲𝗽𝗼 𝗧𝗲𝘀𝘁 𝗛𝗼𝘄 𝗧𝗼 𝗚𝗼𝗼𝗴𝗹𝗲  🔗</strong></h2></center>

    #@markdown <font color=ORANGE>🔗 Please enter the GitHub repository URL: 🔗
    GITHUB_URL = os.environ.get('GITHUB_URL', "https://github.com/Howtog41/text-leech-bot.git")  #@param {type:"string"}

    # Determine base directory based on environment
    base_dir = './repo'  # Save repo in ./repo directory relative to current directory

    # Function to clone or update the repository
    def clone_or_update_repo(repo_url, base_directory):
        repo_name = os.path.basename(repo_url).replace('.git', '')
        project_dir = os.path.join(base_directory, repo_name)

        # Check if the repository directory exists
        if os.path.exists(project_dir):
            print(f"Deleting existing repository at: {project_dir} ...")
            shutil.rmtree(project_dir)
            print("Deleted existing repository successfully!")

        # Clone the repository
        print(f"Cloning repository from {repo_url}...")
        clone_cmd = f"git clone {repo_url} {project_dir}"
        os.system(clone_cmd)
        print("Repository cloned successfully!")

        return project_dir

    # Clone or update the repository
    project_dir = clone_or_update_repo(GITHUB_URL, base_dir)

    # Navigate to the project directory
    print(f"Entering project directory: {os.path.basename(project_dir)}...")
    os.chdir(project_dir)
    print("Entered project directory successfully!")

    #@markdown <font color=ORANGE>🔧 Please enter the requirements.txt file path: 🔧
    PIP_INSTALL = os.environ.get('PIP_INSTALL', "requirements.txt")  #@param {type:"string"}

    # Install required dependencies
    print("Installing required dependencies...")
    os.system(f"pip install -r {PIP_INSTALL}")
    print("All requirements installed successfully!")

    #@markdown ### <font color=ORANGE>🔧 Environment Variables 🔧

    #@markdown <center> <font color=green>✍️ Paste Your Telegram API ID From ≫ my.telegram.org <img src='https://i.ibb.co/ZLbRGmT/Picsart-24-02-16-14-30-48-873.png' height="40" /> </center> { display-mode: "form" }

    API_ID = os.environ.get('API_ID', "")  #@param {type:"string"}
    os.environ['API_ID'] = API_ID

    #@markdown <center> </font> <font color=green>✍️ Paste Your Telegram API HASH From ≫ my.telegram.org <img src='https://i.ibb.co/ZLbRGmT/Picsart-24-02-16-14-30-48-873.png' height="40" /> </center> { display-mode: "form" }

    API_HASH = os.environ.get('API_HASH', "")  #@param {type:"string"}
    os.environ['API_HASH'] = API_HASH

    #@markdown <center> </font> <font color=green>✍️ Paste Your Telegram BOT TOKEN From ≫ @BotFather <img src='https://i.ibb.co/ZLbRGmT/Picsart-24-02-16-14-30-48-873.png' height="40" /> </center> { display-mode: "form" }

    BOT_TOKEN = os.environ.get('BOT_TOKEN', "")  #@param {type:"string"}
    os.environ['BOT_TOKEN'] = BOT_TOKEN

    #@markdown <font color=ORANGE>🔧 Please enter the Profile command: 🔧
    RUN_COMMAND = os.environ.get('RUN_COMMAND', "python3 modules/main.py")  #@param {type:"string"}

    # Run the bot
    print(f"Running command: {RUN_COMMAND} ...")
    os.system(RUN_COMMAND)
    print("✔️ Execution completed!")

if __name__ == '__main__':
    # Start Flask server in a separate thread
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    print("✅ Flask health check server started")
    
    # Run the main bot code
    main()
