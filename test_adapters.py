#!/usr/bin/env python3
"""
Test script for Git Provider Adapters

Run this to verify the adapters work with your credentials.
"""

import asyncio
import os
import sys
import yaml

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from git_provider import create_provider, ProviderType, ProviderError


async def test_github():
    """Test GitHub adapter."""
    print("Testing GitHub adapter...")
    
    # Load config
    config_path = os.path.join(os.path.dirname(__file__), 'config.yaml')
    if not os.path.exists(config_path):
        print(f"Config not found at {config_path}")
        print("Copy config.yaml.example to config.yaml and fill in your credentials")
        return False
    
    with open(config_path) as f:
        config = yaml.safe_load(f)
    
    github_config = config.get('github', {})
    if not github_config.get('token'):
        print("GitHub token not configured in config.yaml")
        return False
    
    try:
        provider = create_provider(ProviderType.GITHUB, github_config)
        await provider.authenticate()
        print("✓ Authentication successful")
        
        # Test rate limit
        rate_limit = await provider.check_rate_limit()
        print(f"✓ Rate limit: {rate_limit}")
        
        # Test repository access (use a test repo)
        repos = config.get('repositories', [])
        if repos:
            repo = repos[0]
            owner, name = repo.split('/')
            print(f"Testing with repository: {owner}/{name}")
            
            # Get repository
            repository = await provider.get_repository(owner, name)
            print(f"✓ Repository: {repository.full_name} ({repository.default_branch})")
            
            # List eligible issues
            issues = await provider.list_eligible_issues(owner, name, label=config.get('labels', {}).get('eligible', 'agent'), limit=5)
            print(f"✓ Eligible issues: {len(issues)}")
            for issue in issues:
                print(f"  - #{issue.number}: {issue.title} [{', '.join(issue.labels)}]")
            
            # Get labels
            labels = await provider.get_labels(owner, name)
            print(f"✓ Labels: {len(labels)}")
            for label in labels[:5]:
                print(f"  - {label.name} (#{label.color})")
        
        return True
        
    except ProviderError as e:
        print(f"✗ Provider error ({e.category}): {e}")
        return False
    except Exception as e:
        print(f"✗ Unexpected error: {e}")
        return False


async def test_gitlab():
    """Test GitLab adapter."""
    print("\nTesting GitLab adapter...")
    
    config_path = os.path.join(os.path.dirname(__file__), 'config.yaml')
    if not os.path.exists(config_path):
        print(f"Config not found at {config_path}")
        return False
    
    with open(config_path) as f:
        config = yaml.safe_load(f)
    
    gitlab_config = config.get('gitlab', {})
    if not gitlab_config.get('token'):
        print("GitLab token not configured in config.yaml")
        return False
    
    try:
        provider = create_provider(ProviderType.GITLAB, gitlab_config)
        await provider.authenticate()
        print("✓ Authentication successful")
        
        # Test rate limit
        rate_limit = await provider.check_rate_limit()
        print(f"✓ Rate limit: {rate_limit}")
        
        # Test repository access
        repos = config.get('repositories', [])
        if repos:
            repo = repos[0]
            owner, name = repo.split('/')
            print(f"Testing with repository: {owner}/{name}")
            
            # Get repository
            repository = await provider.get_repository(owner, name)
            print(f"✓ Repository: {repository.full_name} ({repository.default_branch})")
            
            # List eligible issues
            issues = await provider.list_eligible_issues(owner, name, label=config.get('labels', {}).get('eligible', 'agent'), limit=5)
            print(f"✓ Eligible issues: {len(issues)}")
            for issue in issues:
                print(f"  - #{issue.number}: {issue.title} [{', '.join(issue.labels)}]")
        
        return True
        
    except ProviderError as e:
        print(f"✗ Provider error ({e.category}): {e}")
        return False
    except Exception as e:
        print(f"✗ Unexpected error: {e}")
        return False


async def main():
    """Run tests based on configured provider."""
    config_path = os.path.join(os.path.dirname(__file__), 'config.yaml')
    if not os.path.exists(config_path):
        print("No config.yaml found. Copy config.yaml.example and configure.")
        return
    
    with open(config_path) as f:
        config = yaml.safe_load(f)
    
    provider_type = config.get('provider', 'github')
    
    if provider_type in ('github', 'both'):
        success = await test_github()
        if not success:
            sys.exit(1)
    
    if provider_type in ('gitlab', 'both'):
        success = await test_gitlab()
        if not success:
            sys.exit(1)
    
    print("\n✓ All tests passed!")


if __name__ == '__main__':
    asyncio.run(main())