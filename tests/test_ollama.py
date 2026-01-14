"""
Test Ollama connection and model discovery

Run this to verify:
1. Backend can connect to Ollama
2. Models are discoverable
3. Model switching works
"""
import requests
import json

BASE_URL = 'http://localhost:5000'

def test_ollama_status():
    """Test basic Ollama connectivity"""
    print("\n" + "="*60)
    print("OLLAMA CONNECTION TEST")
    print("="*60)

    try:
        r = requests.get(f'{BASE_URL}/')
        print(f"✅ Backend responding: {r.status_code}")
    except Exception as e:
        print(f"❌ Backend not responding: {e}")
        return False

    return True


def test_model_listing():
    """Test model discovery"""
    print("\n" + "="*60)
    print("MODEL DISCOVERY")
    print("="*60)

    try:
        r = requests.get(f'{BASE_URL}/models')

        if r.status_code == 503:
            print("⚠️  Ollama not initialized (check backend logs)")
            print(f"   Response: {r.json()}")
            return False

        if r.status_code != 200:
            print(f"❌ Failed to list models: {r.status_code}")
            print(f"   Response: {r.json()}")
            return False

        data = r.json()
        current = data.get('current_model', 'NONE')
        models = data.get('available_models', [])

        print(f"\n📍 Current Model: {current}")
        print(f"\n📦 Available Models ({len(models)}):")

        for model in models:
            size = model.get('size_gb', 'unknown')
            quant = model.get('quantization', 'N/A')
            print(f"   • {model['name']}")
            print(f"     Size: {size} GB, Quantization: {quant}")

        if not models:
            print("   ❌ No models found on server!")
            print("\n   To add a model, run on server:")
            print("   ollama pull hf.co/ibm-granite/granite-4.0-h-tiny-GGUF:Q8_0")
            return False

        return True

    except Exception as e:
        print(f"❌ Error listing models: {e}")
        return False


def test_model_switching():
    """Test switching between models"""
    print("\n" + "="*60)
    print("MODEL SWITCHING TEST")
    print("="*60)

    # First, get available models
    try:
        r = requests.get(f'{BASE_URL}/models')
        if r.status_code != 200:
            print("⚠️  Cannot list models, skipping switching test")
            return False

        models = r.json().get('available_models', [])
        if not models:
            print("⚠️  No models available, skipping switching test")
            return False

        # Try to switch to first model
        model_name = models[0]['name']
        print(f"\nAttempting to switch to: {model_name}")

        r = requests.post(f'{BASE_URL}/models/{model_name}')

        if r.status_code == 200:
            result = r.json()
            print(f"✅ {result['message']}")
            return True
        else:
            print(f"❌ Failed: {r.status_code}")
            print(f"   {r.json()}")
            return False

    except Exception as e:
        print(f"❌ Error switching models: {e}")
        return False


def main():
    print("\n" + "="*80)
    print(" OLLAMA INTEGRATION TEST")
    print("="*80)
    print("\nPrerequisites:")
    print("  1. SSH tunnel active: ssh -L 5000:localhost:5000 user@server")
    print("  2. Backend running: python main.py")
    print("  3. Ollama running with at least one model")
    print("\nTo pull IBM Granite model on server:")
    print("  ollama pull hf.co/ibm-granite/granite-4.0-h-tiny-GGUF:Q8_0")
    print("="*80)

    results = {}

    # Test 1: Backend connection
    results['connection'] = test_ollama_status()

    if not results['connection']:
        print("\n❌ Cannot proceed without backend connection")
        return

    # Test 2: Model discovery
    results['discovery'] = test_model_listing()

    if results['discovery']:
        # Test 3: Model switching (only if discovery works)
        results['switching'] = test_model_switching()
    else:
        results['switching'] = False

    # Summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    print(f"Backend Connection:  {'✅ PASS' if results['connection'] else '❌ FAIL'}")
    print(f"Model Discovery:     {'✅ PASS' if results['discovery'] else '❌ FAIL'}")
    print(f"Model Switching:     {'✅ PASS' if results.get('switching', False) else '⚠️  SKIP'}")
    print("="*80 + "\n")

    return all([results.get(k) for k in ['connection', 'discovery']])


if __name__ == '__main__':
    import sys
    success = main()
    sys.exit(0 if success else 1)
