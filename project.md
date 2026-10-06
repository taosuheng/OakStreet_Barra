This is a utility library to download and process Barra risk model. We'll use CNE5 as example for development.
The barra cne5 raw file is saved on ftp://ftp.barra.com/cne5/
1.Every day it shall fetch following files from the ftp
    A. CNE5_CountryHolidays.yyyymmdd, if it exists
    B. SMD_CNE5S_100_yymmdd.zip
    C. SMD_CNE5L_100_yymmdd.zip
    D. FPD_CNE5L_yymmdd.zip
    E. SMD_CNE5_Market_Data_yymmdd.zip
    F. SMD_CNE5L_100_UnadjCov_yymmdd.zip
2.Unzip all the files and save them under <Choosen Folder>/yyyy folder
3.allow download a single day's data, or a batch download for a range of days   
4.FTP username and password will be given in environment variables
5.Explicit support Proxy                                             