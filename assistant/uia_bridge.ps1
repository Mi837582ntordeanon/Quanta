param([Parameter(Mandatory = $true)][string]$RequestBase64)
$ErrorActionPreference = 'Stop'
$utf8 = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8

function Get-Value($Element, [string]$Property) {
    try { return $Element.Current.$Property } catch { return $null }
}

function Get-WindowElement([long]$Handle) {
    if ($Handle -le 0) { $Handle = [int64][QuantaWindowNative]::GetForegroundWindow().ToInt64() }
    if ($Handle -le 0) { throw 'Не удалось определить активное окно.' }
    $element = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$Handle)
    if ($null -eq $element) { throw 'Окно уже закрыто или недоступно.' }
    return $element
}

function Get-Node($Element, [int[]]$Path, [int]$Depth, [int]$MaxDepth, $State) {
    if ($State.count -ge $State.limit) { return $null }
    $State.count++
    $isPassword = [bool](Get-Value $Element 'IsPassword')
    $name = if ($isPassword) { '[поле пароля скрыто]' } else { [string](Get-Value $Element 'Name') }
    $rect = Get-Value $Element 'BoundingRectangle'
    $rectangle = $null
    if ($null -ne $rect -and $rect.Width -gt 0 -and $rect.Height -gt 0) {
        $rectangle = @{ x = [int][Math]::Round($rect.X); y = [int][Math]::Round($rect.Y); width = [int][Math]::Round($rect.Width); height = [int][Math]::Round($rect.Height) }
    }
    $node = @{
        path = @($Path)
        name = $name
        type = [string](Get-Value $Element 'ControlType').ProgrammaticName
        automation_id = [string](Get-Value $Element 'AutomationId')
        class_name = [string](Get-Value $Element 'ClassName')
        enabled = [bool](Get-Value $Element 'IsEnabled')
        offscreen = [bool](Get-Value $Element 'IsOffscreen')
        password = $isPassword
        hwnd = [int64](Get-Value $Element 'NativeWindowHandle')
        rect = $rectangle
        children = @()
    }
    if ($Depth -lt $MaxDepth -and $State.count -lt $State.limit) {
        $children = [System.Collections.Generic.List[object]]::new()
        $child = $State.walker.GetFirstChild($Element)
        $index = 0
        while ($null -ne $child -and $State.count -lt $State.limit) {
            $childPath = @($Path) + @($index)
            $childNode = Get-Node $child $childPath ($Depth + 1) $MaxDepth $State
            if ($null -ne $childNode) { $children.Add($childNode) }
            $child = $State.walker.GetNextSibling($child)
            $index++
        }
        $node.children = @($children.ToArray())
    }
    return $node
}

try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class QuantaWindowNative { [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow(); [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow); [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd); }'
    $json = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($RequestBase64))
    $request = $json | ConvertFrom-Json
    $root = [System.Windows.Automation.AutomationElement]::RootElement
    $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
    $response = @{ ok = $true }

    switch ([string]$request.op) {
        'self_test' {
            $response.available = $null -ne $root
            $response.platform = 'Windows UI Automation / Control View'
        }
        'focus_window' {
            $window = Get-WindowElement ([long]$request.hwnd)
            $handle = [IntPtr]([long](Get-Value $window 'NativeWindowHandle'))
            [QuantaWindowNative]::ShowWindowAsync($handle, 9) | Out-Null
            [QuantaWindowNative]::SetForegroundWindow($handle) | Out-Null
            $response.hwnd = [int64](Get-Value $window 'NativeWindowHandle')
            $response.title = [string](Get-Value $window 'Name')
        }
        'windows' {
            $items = [System.Collections.Generic.List[object]]::new()
            $all = $root.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
            for ($i = 0; $i -lt $all.Count -and $items.Count -lt [int]$request.limit; $i++) {
                $item = $all.Item($i)
                $handle = [int64](Get-Value $item 'NativeWindowHandle')
                $title = [string](Get-Value $item 'Name')
                $processId = [int](Get-Value $item 'ProcessId')
                if ($handle -gt 0 -and ($title.Trim() -or $processId -gt 0)) {
                    $items.Add(@{
                        hwnd = $handle; title = $title; process_id = $processId
                        class_name = [string](Get-Value $item 'ClassName')
                        type = [string](Get-Value $item 'ControlType').ProgrammaticName
                    })
                }
            }
            $response.windows = @($items.ToArray())
        }
        'tree' {
            $window = Get-WindowElement ([long]$request.hwnd)
            $actualHandle = [int64](Get-Value $window 'NativeWindowHandle')
            $state = @{ walker = $walker; count = 0; limit = [int]$request.limit }
            $response.window = @{
                hwnd = $actualHandle
                title = [string](Get-Value $window 'Name')
                process_id = [int](Get-Value $window 'ProcessId')
            }
            $response.tree = Get-Node $window @() 0 ([int]$request.max_depth) $state
            $response.node_count = $state.count
            $response.truncated = $state.count -ge $state.limit
        }
        'act' {
            if ([long]$request.hwnd -le 0) { throw 'Для действия нужен HWND из прочитанного дерева интерфейса.' }
            if (-not $request.expected_name -and -not $request.expected_type -and -not $request.expected_automation_id) {
                throw 'Для действия нужна контрольная характеристика элемента из дерева.'
            }
            $target = Get-WindowElement ([long]$request.hwnd)
            foreach ($index in @($request.path)) {
                $child = $walker.GetFirstChild($target)
                for ($n = 0; $n -lt [int]$index -and $null -ne $child; $n++) { $child = $walker.GetNextSibling($child) }
                if ($null -eq $child) { throw 'Элемент интерфейса изменился; заново прочитай дерево.' }
                $target = $child
            }
            $isPassword = [bool](Get-Value $target 'IsPassword')
            $name = if ($isPassword) { '[поле пароля скрыто]' } else { [string](Get-Value $target 'Name') }
            $type = [string](Get-Value $target 'ControlType').ProgrammaticName
            $automationId = [string](Get-Value $target 'AutomationId')
            if ($request.expected_name -and $name -ne [string]$request.expected_name) { throw 'Заголовок элемента изменился; действие отменено.' }
            if ($request.expected_type -and $type -ne [string]$request.expected_type) { throw 'Тип элемента изменился; действие отменено.' }
            if ($request.expected_automation_id -and $automationId -ne [string]$request.expected_automation_id) { throw 'Automation ID элемента изменился; действие отменено.' }
            if (-not [bool](Get-Value $target 'IsEnabled')) { throw 'Элемент выключен или недоступен.' }
            switch ([string]$request.action) {
                'invoke' { ([System.Windows.Automation.InvokePattern]$target.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)).Invoke() }
                'set_value' {
                    if ($isPassword) { throw 'Ввод в поля пароля заблокирован.' }
                    $pattern = [System.Windows.Automation.ValuePattern]$target.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
                    $pattern.SetValue([string]$request.value)
                }
                'toggle' { ([System.Windows.Automation.TogglePattern]$target.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern)).Toggle() }
                'select' { ([System.Windows.Automation.SelectionItemPattern]$target.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern)).Select() }
                'focus' { $target.SetFocus() }
                default { throw 'Недопустимая операция UI Automation.' }
            }
            $response.action = [string]$request.action
            $response.element = $name
            $response.type = $type
        }
        default { throw 'Неизвестная операция Windows UI Automation.' }
    }
    $response | ConvertTo-Json -Depth 14 -Compress
} catch {
    @{ ok = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress
    exit 1
}
