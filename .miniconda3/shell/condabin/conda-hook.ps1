$Env:CONDA_EXE = "/Users/diveshjoshi_25/Desktop/Data/dys_ai_bot/backend/.miniconda3/bin/conda"
$Env:_CONDA_EXE = "/Users/diveshjoshi_25/Desktop/Data/dys_ai_bot/backend/.miniconda3/bin/conda"
$Env:_CE_M = $null
$Env:_CE_CONDA = $null
$Env:CONDA_PYTHON_EXE = "/Users/diveshjoshi_25/Desktop/Data/dys_ai_bot/backend/.miniconda3/bin/python"
$Env:_CONDA_ROOT = "/Users/diveshjoshi_25/Desktop/Data/dys_ai_bot/backend/.miniconda3"
$CondaModuleArgs = @{ChangePs1 = $True}

Import-Module "$Env:_CONDA_ROOT\shell\condabin\Conda.psm1" -ArgumentList $CondaModuleArgs

Remove-Variable CondaModuleArgs