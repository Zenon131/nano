import os
from tavily import TavilyClient
from dotenv import load_dotenv

load_dotenv()

def web_search(query: str):
    """
    Searches the web using the Tavily API to find current information, 
    news, or documentation. Returns a list of relevant results including 
    titles, URLs, and content snippets.
    """
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return "Error: TAVILY_API_KEY not found in environment variables."
    
    try:
        tavily = TavilyClient(api_key=api_key)
        # We use the 'search' method which returns a clean list of results
        response = tavily.search(query=query, search_depth="advanced", max_results=5)
        
        results = response.get("results", [])
        if not results:
            return f"No search results found for: {query}"
        
        formatted_results = []
        for i, res in enumerate(results, 1):
            formatted_results.append(
                f"[{i}] {res['title']}\nURL: {res['url']}\nContent: {res['content']}\n"
            )
        
        return "\n\n".join(formatted_results)
    except Exception as e:
        return f"Error performing web search: {str(e)}"
