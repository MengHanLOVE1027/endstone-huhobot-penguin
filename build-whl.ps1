Set-Location 'C:\Users\HeYuHan\Desktop\Plugins-Dev\EndStone\Projects\endstone-huhobot-penguin'

Write-Host "=== Cleaning dist ===" -ForegroundColor Cyan
Remove-Item '.\dist\*' -Recurse -ErrorAction SilentlyContinue

Write-Host "=== Building wheel ===" -ForegroundColor Cyan
python -m build --wheel

Write-Host "=== Deploying to server ===" -ForegroundColor Cyan
Remove-Item 'C:\Users\HeYuHan\Desktop\Plugins-Dev\EndStone\bedrock_server\plugins\endstone_huhobot_penguin*.whl' -ErrorAction SilentlyContinue
Copy-Item -Path '.\dist\endstone_huhobot_penguin*.whl' -Destination 'C:\Users\HeYuHan\Desktop\Plugins-Dev\EndStone\bedrock_server\plugins'

Write-Host "=== Done ===" -ForegroundColor Green
Get-ChildItem 'C:\Users\HeYuHan\Desktop\Plugins-Dev\EndStone\bedrock_server\plugins\endstone_huhobot_penguin*.whl'
