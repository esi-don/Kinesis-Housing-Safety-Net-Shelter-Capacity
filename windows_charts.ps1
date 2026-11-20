param([Parameter(Mandatory=$true)][string]$SpecFile,[Parameter(Mandatory=$true)][string]$OutputDir)
$ErrorActionPreference='Stop'
Add-Type -Path 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\System.Windows.Forms.DataVisualization.dll'
Add-Type -AssemblyName System.Drawing
$culture=[System.Globalization.CultureInfo]::InvariantCulture
$specs=Get-Content -LiteralPath $SpecFile -Raw -Encoding UTF8 | ConvertFrom-Json
foreach($spec in $specs){
    $chart=New-Object System.Windows.Forms.DataVisualization.Charting.Chart
    try {
        $chart.Width=1800; $chart.Height=950; $chart.BackColor=[System.Drawing.Color]::White
        $chart.AntiAliasing='All'; $chart.TextAntiAliasingQuality='High'
        $area=New-Object System.Windows.Forms.DataVisualization.Charting.ChartArea 'main'
        $area.Position=New-Object System.Windows.Forms.DataVisualization.Charting.ElementPosition(6,20,91,59)
        $area.BackColor=[System.Drawing.Color]::White
        $area.AxisY.Title=$spec.ylabel
        $area.AxisY.TitleFont=New-Object System.Drawing.Font('Arial',14)
        $area.AxisX.TitleFont=New-Object System.Drawing.Font('Arial',13)
        $area.AxisY.LabelStyle.Font=New-Object System.Drawing.Font('Arial',12)
        $area.AxisX.LabelStyle.Font=New-Object System.Drawing.Font('Arial',12)
        $area.AxisY.MajorGrid.LineColor=[System.Drawing.ColorTranslator]::FromHtml('#dfe5ec')
        $area.AxisX.MajorGrid.Enabled=$false
        $area.AxisY.LineColor=[System.Drawing.Color]::Gray
        $area.AxisX.LineColor=[System.Drawing.Color]::Gray
        $area.AxisY.IsStartedFromZero=$false
        $area.AxisY.LabelStyle.Format=if($spec.percent){'P0'}else{'N0'}
        if($null -ne $spec.ymin){$area.AxisY.Minimum=[double]$spec.ymin}
        if($null -ne $spec.ymax){$area.AxisY.Maximum=[double]$spec.ymax}
        if($null -ne $spec.ystep){$area.AxisY.Interval=[double]$spec.ystep}
        if($spec.xlabel){$area.AxisX.Title=$spec.xlabel}
        if($spec.categories){$area.AxisX.Interval=1}else{
            $dateValues=@($spec.series | ForEach-Object { $_.points | ForEach-Object { [datetime]::ParseExact([string]$_[0],'yyyy-MM-dd',$culture) } })
            $extent=$dateValues | Measure-Object -Minimum -Maximum
            $days=([datetime]$extent.Maximum-[datetime]$extent.Minimum).TotalDays
            if($days -gt 1095){$area.AxisX.IntervalType='Years'; $area.AxisX.Interval=1; $area.AxisX.LabelStyle.Format='yyyy'}
            elseif($days -gt 180){$area.AxisX.IntervalType='Months'; $area.AxisX.Interval=2; $area.AxisX.LabelStyle.Format='MMM yyyy'}
            else{$area.AxisX.IntervalType='Days'; $area.AxisX.Interval=14; $area.AxisX.LabelStyle.Format='dd MMM'}
        }
        $chart.ChartAreas.Add($area)
        foreach($details in @(@($spec.title,25,1,8),@($spec.subtitle,15,10,6),@($spec.note,12,93,5))){
            $title=New-Object System.Windows.Forms.DataVisualization.Charting.Title
            $title.Text=[string]$details[0]
            $title.Font=New-Object System.Drawing.Font('Arial',[single]$details[1])
            $title.ForeColor=[System.Drawing.ColorTranslator]::FromHtml('#1b3448')
            $title.Alignment='MiddleLeft'
            $title.Position=New-Object System.Windows.Forms.DataVisualization.Charting.ElementPosition(9,[single]$details[2],88,[single]$details[3])
            [void]$chart.Titles.Add($title)
        }
        $legend=New-Object System.Windows.Forms.DataVisualization.Charting.Legend 'legend'
        $legend.Font=New-Object System.Drawing.Font('Arial',12)
        $legend.Position=New-Object System.Windows.Forms.DataVisualization.Charting.ElementPosition(9,82,88,9)
        $legend.LegendStyle='Table'; $legend.TableStyle='Wide'
        $chart.Legends.Add($legend)
        $i=0
        foreach($item in $spec.series){
            $series=New-Object System.Windows.Forms.DataVisualization.Charting.Series ('series'+$i)
            $i++
            $series.Legend='legend'; $series.LegendText=$item.label
            $series.Color=[System.Drawing.ColorTranslator]::FromHtml($item.color)
            $series.BorderWidth=3
            $series.ChartArea='main'
            if($item.dash){$series.BorderDashStyle='Dash'}
            if($item.kind -eq 'bar'){
                $series.ChartType='Column'; $series.IsValueShownAsLabel=$true
                $series.LabelFormat='N0'; $series.Font=New-Object System.Drawing.Font('Arial',13)
            }elseif($item.kind -eq 'band'){
                $series.ChartType='Range'; $series.YValuesPerPoint=2
                $series.Color=[System.Drawing.Color]::FromArgb(35,$series.Color)
                $series.BorderWidth=0
            }elseif($item.kind -eq 'range'){
                $series.ChartType='ErrorBar'; $series.YValuesPerPoint=3
                $series.BorderWidth=2
                $series['ErrorBarCenterMarkerStyle']='Circle'
                $series['ErrorBarStyle']='Both'
                $series.MarkerSize=10
            }else{$series.ChartType='Line'}
            if($item.kind -ne 'bar'){$series.XValueType='DateTime'}
            foreach($p in $item.points){
                $point=New-Object System.Windows.Forms.DataVisualization.Charting.DataPoint
                if($item.kind -eq 'bar'){
                    $point.XValue=[double]$p[0]
                    $point.AxisLabel=[string]$spec.categories[[int]$p[0]]
                }else{$point.XValue=([datetime]::ParseExact([string]$p[0],'yyyy-MM-dd',$culture)).ToOADate()}
                if($null -eq $p[1]){
                    $point.YValues=[double[]]@(0); $point.IsEmpty=$true
                }elseif($item.kind -eq 'band'){
                    $point.YValues=[double[]]@([double]$p[2],[double]$p[1])
                }elseif($item.kind -eq 'range'){
                    $point.YValues=[double[]]@([double]$p[3],[double]$p[1],[double]$p[2])
                }else{$point.YValues=[double[]]@([double]$p[1])}
                [void]$series.Points.Add($point)
            }
            $chart.Series.Add($series)
        }
        $destination=Join-Path $OutputDir ($spec.name+'.png')
        $chart.SaveImage($destination,'Png')
        Write-Output $destination
    } finally {$chart.Dispose()}
}
