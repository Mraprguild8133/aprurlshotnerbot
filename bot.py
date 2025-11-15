import os
import logging
import requests
import re
import hashlib
import threading
from datetime import datetime
from flask import Flask, render_template, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration from environment variables
class Config:
    BOT_TOKEN = os.environ.get('BOT_TOKEN')
    BITLY_TOKEN = os.environ.get('BITLY_TOKEN', '')
    CUTTLY_API = os.environ.get('CUTTLY_API', '')
    GPLINKS_API = os.environ.get('GPLINKS_API', '')
    USE_WEBHOOK = os.environ.get('USE_WEBHOOK', 'true').lower() == 'true'
    PORT = int(os.environ.get('PORT', 10000))
    WEBHOOK_URL = os.environ.get('WEBHOOK_URL', '')
    WELCOME_IMAGE_URL = os.environ.get('WELCOME_IMAGE_URL', 'https://iili.io/Kcbrql9.th.jpg')
    
    SUPPORTED_SERVICES = {
        'bitly': {
            'name': 'Bitly',
            'api_url': 'https://api-ssl.bitly.com/v4/shorten',
            'requires_key': True
        },
        'tinyurl': {
            'name': 'TinyURL',
            'api_url': 'http://tinyurl.com/api-create.php',
            'requires_key': False
        },
        'cuttly': {
            'name': 'Cuttly',
            'api_url': 'https://cutt.ly/api/api.php',
            'requires_key': True
        },
        'gplinks': {
            'name': 'GPLinks',
            'api_url': 'https://gplinks.in/api',
            'requires_key': True
        }
    }

config = Config()

# Initialize Flask app
app = Flask(__name__)

# Global bot instance
bot_application = None
url_cache = {}

def check_service_status():
    """Check status of each service"""
    status = {
        'bitly': bool(config.BITLY_TOKEN),
        'cuttly': bool(config.CUTTLY_API),
        'gplinks': bool(config.GPLINKS_API)
    }
    connected_count = sum(status.values()) + 1  # +1 for TinyURL
    return status, connected_count

@app.route('/')
def index():
    """Main status page"""
    services_status, connected_count = check_service_status()
    
    context = {
        'bitly_status': services_status['bitly'],
        'cuttly_status': services_status['cuttly'],
        'gplinks_status': services_status['gplinks'],
        'bitly_key': config.BITLY_TOKEN,
        'cuttly_key': config.CUTTLY_API,
        'gplinks_key': config.GPLINKS_API,
        'connected_services': connected_count,
        'current_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    return render_template('index.html', **context)

@app.route('/api/status')
def api_status():
    """JSON API endpoint for status"""
    services_status, connected_count = check_service_status()
    
    return {
        'status': 'online',
        'timestamp': datetime.now().isoformat(),
        'services': {
            'bitly': {
                'connected': services_status['bitly'],
                'has_key': bool(config.BITLY_TOKEN)
            },
            'tinyurl': {
                'connected': True,
                'has_key': False
            },
            'cuttly': {
                'connected': services_status['cuttly'],
                'has_key': bool(config.CUTTLY_API)
            },
            'gplinks': {
                'connected': services_status['gplinks'],
                'has_key': bool(config.GPLINKS_API)
            }
        },
        'summary': {
            'total_services': 4,
            'connected_services': connected_count
        }
    }

@app.route('/health')
def health_check():
    """Health check endpoint for Render"""
    bot_status = "running" if bot_application else "stopped"
    return {
        'status': 'healthy', 
        'bot': bot_status,
        'timestamp': datetime.now().isoformat()
    }

class URLShortenerBot:
    def __init__(self, token):
        self.token = token
        self.application = Application.builder().token(token).build()
        
        # Add handlers
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("help", self.help))
        self.application.add_handler(CommandHandler("shorten", self.shorten))
        self.application.add_handler(CommandHandler("status", self.status))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        self.application.add_handler(CallbackQueryHandler(self.button_handler))
        
        # Add error handler
        self.application.add_error_handler(self.error_handler)
    
    def is_valid_url(self, url: str) -> bool:
        """Enhanced URL validation"""
        pattern = re.compile(
            r'^https?://'  # http:// or https://
            r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'  # domain...
            r'localhost|'  # localhost...
            r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # ...or ip
            r'(?::\d+)?'  # optional port
            r'(?:/?|[/?]\S+)$', re.IGNORECASE)
        return re.match(pattern, url) is not None
    
    def generate_url_id(self, url: str) -> str:
        """Generate a short unique ID for the URL to avoid long callback data"""
        url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
        return url_hash
    
    def store_url(self, url: str) -> str:
        """Store URL in cache and return short ID"""
        url_id = self.generate_url_id(url)
        url_cache[url_id] = url
        return url_id
    
    def get_url(self, url_id: str) -> str:
        """Retrieve URL from cache using short ID"""
        return url_cache.get(url_id, '')
    
    async def error_handler(self, update: Update, context: CallbackContext):
        """Handle errors in the telegram bot"""
        logger.error(f"Exception while handling an update: {context.error}", exc_info=context.error)
        
        try:
            if update and update.effective_message:
                await update.effective_message.reply_text("❌ An error occurred. Please try again.")
        except Exception as e:
            logger.error(f"Error while sending error message: {e}")

    def is_image_accessible(self, url: str) -> bool:
        """Check if the welcome image URL is accessible"""
        try:
            response = requests.head(url, timeout=10)
            return response.status_code == 200
        except:
            return False

    def shorten_url(self, url, service):
        """Shorten URL using the specified service"""
        try:
            if not self.is_valid_url(url):
                return None

            logger.info(f"Shortening URL with {service}: {url}")

            if service == 'bitly':
                if not config.BITLY_TOKEN:
                    return None
                
                headers = {'Authorization': f'Bearer {config.BITLY_TOKEN}', 'Content-Type': 'application/json'}
                data = {'long_url': url}
                response = requests.post(config.SUPPORTED_SERVICES[service]['api_url'], headers=headers, json=data, timeout=10)
                if response.status_code == 200:
                    return response.json()['link']
                return None
            
            elif service == 'tinyurl':
                params = {'url': url}
                response = requests.get(config.SUPPORTED_SERVICES[service]['api_url'], params=params, timeout=10)
                if response.status_code == 200:
                    return response.text.strip()
                return None
            
            elif service == 'cuttly':
                if not config.CUTTLY_API:
                    return None
                
                params = {'key': config.CUTTLY_API, 'short': url}
                response = requests.get(config.SUPPORTED_SERVICES[service]['api_url'], params=params, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    if data.get('url', {}).get('status') == 7:
                        return data['url']['shortLink']
                return None
            
            elif service == 'gplinks':
                if not config.GPLINKS_API:
                    return None
                
                api_url = "https://gplinks.in/api"
                params = {'api': config.GPLINKS_API, 'url': url}
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36', 'Accept': 'application/json'}
                
                # Try GET request first
                response = requests.get(api_url, params=params, headers=headers, timeout=15)
                if response.status_code == 200:
                    response_text = response.text.strip()
                    if response_text.startswith('http'):
                        return response_text
                    try:
                        json_data = response.json()
                        if json_data.get('status') == 'success':
                            return json_data.get('shortenedUrl') or json_data.get('shorturl')
                    except ValueError:
                        if 'http' in response_text:
                            urls = re.findall(r'https?://[^\s]+', response_text)
                            if urls:
                                return urls[0]
                
                # If GET failed, try POST request
                payload = {'api': config.GPLINKS_API, 'url': url}
                response = requests.post(api_url, data=payload, headers=headers, timeout=15)
                if response.status_code == 200:
                    response_text = response.text.strip()
                    if response_text.startswith('http'):
                        return response_text
                
                return None
            
            return None
            
        except Exception as e:
            logger.error(f"Error shortening URL with {service}: {str(e)}")
            return None
    
    async def start(self, update: Update, context: CallbackContext):
        """Send welcome message when command /start is issued"""
        try:
            user = update.effective_user
            
            welcome_text = f"""
👋 Hello {user.mention_html()}!

**Welcome to URL Shortener Bot!** 🌐

I can shorten your long URLs using various services and help you earn money with shortened links!

✨ **Features:**
• Multiple URL shortening services
• Easy-to-use interface
• Monetization options with GPLinks
• Fast and reliable service

📋 **Available Commands:**
/start - Start the bot
/help - Show help message  
/shorten - Shorten a URL
/status - Check API key status

🚀 **Get Started:**
Simply send me a URL or use /shorten command to begin!
            """
            
            # Try to send with image if available and accessible
            image_sent = False
            if config.WELCOME_IMAGE_URL and self.is_image_accessible(config.WELCOME_IMAGE_URL):
                try:
                    await update.message.reply_photo(
                        photo=config.WELCOME_IMAGE_URL,
                        caption=welcome_text,
                        parse_mode='HTML'
                    )
                    image_sent = True
                except Exception:
                    image_sent = False
            
            if not image_sent:
                await update.message.reply_html(welcome_text)
                
        except Exception as e:
            logger.error(f"Error in start command: {e}")
            await update.message.reply_text("❌ An error occurred. Please try again.")
    
    async def help(self, update: Update, context: CallbackContext):
        """Send help message"""
        try:
            help_text = """
🤖 **URL Shortener Bot Help Guide**

📖 **How to use:**
1. Send me any long URL directly
2. Or use `/shorten <URL>` command
3. Choose your preferred shortening service
4. Get your shortened link instantly!

🛠 **Supported Services:**
✅ **Bitly** - Professional URL shortening with analytics
✅ **TinyURL** - Simple, reliable, no API key required  
✅ **Cuttly** - Advanced analytics and customization
✅ **GPLinks** - Earn money from your shortened links!

💰 **Monetization:**
With GPLinks, you can earn revenue from every click!
Sign up at https://gplinks.in for your API key.
            """
            await update.message.reply_text(help_text, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Error in help command: {e}")
            await update.message.reply_text("❌ An error occurred. Please try again.")
    
    async def status(self, update: Update, context: CallbackContext):
        """Check API key status"""
        try:
            status_text = "🔧 **API Key Status**\n\n"
            
            for service_key, service_info in config.SUPPORTED_SERVICES.items():
                service_name = service_info['name']
                requires_key = service_info['requires_key']
                
                if service_key == 'bitly':
                    has_key = bool(config.BITLY_TOKEN)
                    key_preview = config.BITLY_TOKEN[:8] + '...' if has_key else 'Not set'
                elif service_key == 'cuttly':
                    has_key = bool(config.CUTTLY_API)
                    key_preview = config.CUTTLY_API[:8] + '...' if has_key else 'Not set'
                elif service_key == 'gplinks':
                    has_key = bool(config.GPLINKS_API)
                    key_preview = config.GPLINKS_API[:8] + '...' if has_key else 'Not set'
                else:
                    has_key = True
                    key_preview = "Not required"
                
                status_text += f"**{service_name}**: "
                if requires_key:
                    status_text += "✅" if has_key else "❌"
                else:
                    status_text += "✅"
                status_text += f" ({key_preview})\n"
            
            # Add web dashboard info
            if config.WEBHOOK_URL:
                dashboard_url = config.WEBHOOK_URL.replace(f'/{config.BOT_TOKEN}', '')
                status_text += f"\n🌐 **Web Dashboard:** {dashboard_url}"
            
            await update.message.reply_text(status_text, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Error in status command: {e}")
            await update.message.reply_text("❌ Error checking status")
    
    async def shorten(self, update: Update, context: CallbackContext):
        """Shorten URL from command"""
        try:
            if not context.args:
                await update.message.reply_text("Please provide a URL to shorten. Usage: `/shorten <URL>`", parse_mode='Markdown')
                return
            
            url = ' '.join(context.args)
            await self.process_url(update, url)
        except Exception as e:
            logger.error(f"Error in shorten command: {e}")
            await update.message.reply_text("❌ An error occurred. Please try again.")
    
    async def handle_message(self, update: Update, context: CallbackContext):
        """Handle messages containing URLs"""
        try:
            url = update.message.text.strip()
            
            if not (url.startswith('http://') or url.startswith('https://')):
                await update.message.reply_text("Please send a valid URL starting with http:// or https://")
                return
            
            await self.process_url(update, url)
        except Exception as e:
            logger.error(f"Error handling message: {e}")
            await update.message.reply_text("❌ An error occurred. Please try again.")
    
    async def process_url(self, update: Update, url: str):
        """Process URL and generate shortened versions"""
        try:
            if not self.is_valid_url(url):
                await update.message.reply_text("❌ Please provide a valid URL starting with http:// or https://")
                return
            
            await update.message.reply_chat_action(action="typing")
            
            url_id = self.store_url(url)
            
            keyboard = [
                [
                    InlineKeyboardButton("🌐 Bitly", callback_data=f"s_bitly_{url_id}"),
                    InlineKeyboardButton("🔗 TinyURL", callback_data=f"s_tiny_{url_id}"),
                ],
                [
                    InlineKeyboardButton("📊 Cuttly", callback_data=f"s_cutt_{url_id}"),
                    InlineKeyboardButton("💰 GPLinks", callback_data=f"s_gpl_{url_id}"),
                ],
                [InlineKeyboardButton("🚀 All Services", callback_data=f"s_all_{url_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            display_url = url
            if len(url) > 50:
                display_url = url[:47] + "..."
            
            await update.message.reply_text(
                f"🔗 **Original URL:**\n`{display_url}`\n\n**Choose a service to shorten:**",
                reply_markup=reply_markup,
                disable_web_page_preview=True,
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Error processing URL: {e}")
            await update.message.reply_text("❌ An error occurred while processing your URL. Please try again.")
    
    async def button_handler(self, update: Update, context: CallbackContext):
        """Handle button callbacks"""
        try:
            query = update.callback_query
            await query.answer()
            
            data = query.data
            if data.startswith('s_'):
                parts = data.split('_', 2)
                if len(parts) == 3:
                    _, service_code, url_id = parts
                    
                    service_map = {
                        'bitly': 'bitly',
                        'tiny': 'tinyurl', 
                        'cutt': 'cuttly',
                        'gpl': 'gplinks',
                        'all': 'all'
                    }
                    
                    service = service_map.get(service_code, service_code)
                    url = self.get_url(url_id)
                    
                    if not url:
                        await query.edit_message_text("❌ URL not found. Please try again.")
                        return
                    
                    await query.message.reply_chat_action(action="typing")
                    
                    if service == 'all':
                        await self.send_all_shortened_urls(query, url)
                    else:
                        await self.send_single_shortened_url(query, url, service)
                else:
                    await query.edit_message_text("❌ Invalid request. Please try again.")
            else:
                await query.edit_message_text("❌ Unknown command. Please try again.")
                
        except Exception as e:
            logger.error(f"Error in button handler: {e}")
            try:
                await query.edit_message_text("❌ An error occurred. Please try again.")
            except:
                await query.message.reply_text("❌ An error occurred. Please try again.")
    
    async def send_single_shortened_url(self, query, url: str, service: str):
        """Send shortened URL from a single service"""
        try:
            service_info = config.SUPPORTED_SERVICES.get(service, {})
            service_name = service_info.get('name', service.capitalize())
            
            shortened_url = self.shorten_url(url, service)
            
            if shortened_url:
                message = f"✅ **{service_name}**\n🔗 `{shortened_url}`"
                
                if service == 'gplinks':
                    message += "\n\n💰 *Earn money with this shortened link!*"
                
                await query.edit_message_text(
                    text=message,
                    disable_web_page_preview=True,
                    parse_mode='Markdown'
                )
            else:
                error_msg = f"❌ Failed to shorten URL using {service_name}."
                await query.edit_message_text(text=error_msg)
        except Exception as e:
            logger.error(f"Error sending single shortened URL: {e}")
            await query.edit_message_text("❌ Error generating shortened URL. Please try again.")
    
    async def send_all_shortened_urls(self, query, url: str):
        """Send shortened URLs from all available services"""
        try:
            message = "🔗 **Shortened URLs**\n\n"
            successful_shortens = 0
            
            for service_key, service_info in config.SUPPORTED_SERVICES.items():
                service_name = service_info.get('name', service_key.capitalize())
                shortened_url = self.shorten_url(url, service_key)
                
                if shortened_url:
                    message += f"✅ **{service_name}**\n`{shortened_url}`"
                    if service_key == 'gplinks':
                        message += " 💰"
                    message += "\n\n"
                    successful_shortens += 1
                else:
                    message += f"❌ **{service_name}** - Failed\n\n"
            
            if successful_shortens == 0:
                message = "❌ All services failed. Please try again later."
            else:
                message += f"✅ **{successful_shortens}/{len(config.SUPPORTED_SERVICES)} successful**"
            
            await query.edit_message_text(
                text=message,
                disable_web_page_preview=True,
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Error sending all shortened URLs: {e}")
            await query.edit_message_text("❌ Error generating shortened URLs. Please try again.")
    
    def run_webhook(self):
        """Start the bot with webhook"""
        try:
            logger.info(f"Starting bot webhook on port {config.PORT}...")
            
            if config.WEBHOOK_URL:
                webhook_url = f"{config.WEBHOOK_URL}/{self.token}"
                self.application.bot.set_webhook(
                    url=webhook_url,
                    allowed_updates=Update.ALL_TYPES,
                    drop_pending_updates=True
                )
            
            self.application.run_webhook(
                listen="0.0.0.0",
                port=config.PORT,
                webhook_url=config.WEBHOOK_URL + f'/{self.token}' if config.WEBHOOK_URL else None,
                url_path=self.token
            )
        except Exception as e:
            logger.error(f"Error starting webhook: {e}")
            raise

def start_bot():
    """Start the Telegram bot in webhook mode"""
    global bot_application
    try:
        if config.BOT_TOKEN:
            print("🤖 Starting Telegram Bot...")
            bot_application = URLShortenerBot(config.BOT_TOKEN)
            
            if config.USE_WEBHOOK:
                bot_application.run_webhook()
            else:
                print("🔄 Starting bot in polling mode...")
                bot_application.application.run_polling()
        else:
            print("❌ BOT_TOKEN not set - Telegram bot disabled")
    except Exception as e:
        print(f"❌ Failed to start bot: {e}")

def main():
    """Main function to run the application"""
    print("🚀 URL Shortener Application Starting...")
    print(f"🔧 Port: {config.PORT}")
    print(f"🔧 Webhook Mode: {config.USE_WEBHOOK}")
    print(f"🔧 Webhook URL: {config.WEBHOOK_URL}")
    
    # Start bot in a separate thread if webhook mode
    if config.USE_WEBHOOK and config.BOT_TOKEN:
        bot_thread = threading.Thread(target=start_bot, daemon=True)
        bot_thread.start()
        print("🤖 Bot started in background thread")
    
    # Start Flask app
    print("🌐 Starting Flask web server...")
    app.run(host='0.0.0.0', port=config.PORT, debug=False, use_reloader=False)

if __name__ == '__main__':
    main()
