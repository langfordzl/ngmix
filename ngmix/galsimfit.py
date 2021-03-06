"""
fitting using galsim to create the models
"""

from __future__ import print_function
try:
    xrange
except:
    xrange=range

import numpy

from .fitting import (
    LMSimple,
    TemplateFluxFitter,
    run_leastsq,
    _default_lm_pars,
    print_pars,
    PDEF,CDEF,
)

from . import observation
from .observation import Observation, ObsList, MultiBandObsList

from .priors import LOWVAL,BIGVAL
from .gexceptions import GMixRangeError

class GalsimRunner(object):
    """
    wrapper to generate guesses and run the fitter a few times

    Can be used to run GalsimSimple and SpergelFitter fitters

    parameters
    ----------
    obs: observation
        An instance of Observation, ObsList, or MultiBandObsList
    model: string
        e.g. 'exp', 'spergel'
    guesser: ngmix guesser
        E.g. R50FluxGuesser for 6 parameter models, R50NuFluxGuesser for
        a spergel model
    lm_pars: dict
        parameters for the lm fitter, e.g. maxfev, ftol, xtol
    prior: ngmix prior
        For example when fitting simple models,
        ngmix.priors.PriorSimpleSep can be used as a separable prior on
        center, g, size, flux.

        For spergel, PriorSpergelSep can be used.
    """
    def __init__(self,
                 obs,
                 model,
                 guesser,
                 lm_pars=None,
                 prior=None):

        self.obs=obs
        self.model=model
        self.guesser=guesser
        self.prior=prior

        self.lm_pars={}
        if lm_pars is not None:
            self.lm_pars.update(lm_pars)

    def get_fitter(self):
        return self.fitter

    def go(self, ntry=1):

        fitter=self._create_fitter()
        for i in xrange(ntry):

            guess=self.get_guess()
            fitter.go(guess)

            res=fitter.get_result()
            if res['flags']==0:
                break

        res['ntry'] = i+1
        self.fitter=fitter

    def get_guess(self):

        if self.model=='spergel':
            while True:
                guess=self.guesser()
                nu=guess[5]
                if nu > -0.84 and nu < 3.99:
                    break
        else:
            guess = self.guesser()

        return guess

    def _create_fitter(self):
        if self.model=='spergel':
            return SpergelFitter(
                self.obs,
                lm_pars=self.lm_pars,
                prior=self.prior,
            )
        else:
            return GalsimSimple(
                self.obs,
                self.model,
                lm_pars=self.lm_pars,
                prior=self.prior,
            )

class GalsimSimple(LMSimple):
    """
    Fit using galsim 6 parameter models

    parameters
    ----------
    obs: observation
        An instance of Observation, ObsList, or MultiBandObsList
    model: string
        e.g. 'exp', 'spergel'
    lm_pars: dict, optional
        parameters for the lm fitter, e.g. maxfev, ftol, xtol
    prior: ngmix prior, optional
        For example ngmix.priors.PriorSimpleSep can
        be used as a separable prior on center, g, size, flux.
    """
    def __init__(self, obs, model, **keys):
        self.keys=keys
        self.model_name=model
        self._set_model_class()

        self._set_kobs(obs)
        self._init_model_images()

        self._set_fitting_pars(**keys)
        self._set_prior(**keys)

        self._set_band_pars()
        self._set_totpix()

        self._set_fdiff_size()

    def go(self, guess):
        """
        Run leastsq and set the result
        """

        guess=self._get_guess(guess)

        result = run_leastsq(self._calc_fdiff,
                             guess,
                             self.n_prior_pars,
                             k_space=True,
                             **self.lm_pars)

        result['model'] = self.model_name
        if result['flags']==0:
            result['g'] = result['pars'][2:2+2].copy()
            result['g_cov'] = result['pars_cov'][2:2+2, 2:2+2].copy()
            stat_dict=self.get_fit_stats(result['pars'])
            result.update(stat_dict)

        self._result=result

    def _calc_fdiff(self, pars):
        """

        vector with (model-data)/error.

        The npars elements contain -ln(prior)
        """

        # we cannot keep sending existing array into leastsq, don't know why
        fdiff=numpy.zeros(self.fdiff_size)

        try:


            self._fill_models(pars)

            #print_pars(fdiff[0:5],front="befor fill:")
            start=self._fill_priors(pars, fdiff)
            #print_pars(pars,front="      pars:")
            #print_pars(fdiff[0:start],front="after fill:")

            for band in xrange(self.nband):

                kobs_list=self.mb_kobs[band]
                for kobs in kobs_list:

                    meta =kobs.meta
                    kmodel = meta['kmodel']
                    ierr = meta['ierr']

                    scratch = meta['scratch']

                    # model-data
                    scratch.array[:,:] = kmodel.array[:,:]
                    scratch -= kobs.kimage

                    # (model-data)/err
                    scratch.array.real[:,:] *= ierr.array[:,:]
                    scratch.array.imag[:,:] *= ierr.array[:,:]

                    # now copy into the full fdiff array
                    imsize = scratch.array.size

                    fdiff[start:start+imsize] = scratch.array.real.ravel()

                    start += imsize

                    fdiff[start:start+imsize] = scratch.array.imag.ravel()

                    start += imsize

        except GMixRangeError as err:
            fdiff[:] = LOWVAL

        return fdiff

    def _fill_models(self, pars):
        """
        input pars are in linear space

        Fill the list of lists of gmix objects for the given parameters
        """
        import galsim
        for band,kobs_list in enumerate(self.mb_kobs):
            # pars for this band, in linear space
            band_pars=self.get_band_pars(pars, band)

            for i,kobs in enumerate(kobs_list):

                gal = self.make_model(band_pars)

                meta=kobs.meta

                kmodel=meta['kmodel']

                gal._drawKImage(kmodel)

                kmodel *= kobs.psf.kimage

    """
                if False:
                    #print("kmodel before",kmodel)

                    dk = kmodel.scale
                    dx = numpy.pi/( max(kmodel.array.shape) // 2 * dk )

                    real_prof = galsim.PixelScale(dx).toImage(gal)
                    kmodel = real_prof._setup_image(
                        kmodel, None, None, None, False, numpy.complex128,
                        odd=True,wmult=1.0,
                    )
                    kmodel.setCenter(0,0)
                    #print("kmodel after",kmodel)
                    gal.SBProfile.drawK(kmodel.image.view(), dk)
                else:
    """
    def make_model(self, pars):
        """
        make the galsim model
        """

        model = self.make_round_model(pars)

        shift = pars[0:0+2]
        g1    = pars[2]
        g2    = pars[3]

        # argh another generic error
        try:
            model = model.shear(g1=g1, g2=g2)
        except ValueError as err:
            raise GMixRangeError(str(err))

        model = model.shift(shift)
        return model

    def make_round_model(self, pars):
        """
        make the round galsim model, unshifted
        """

        r50   = pars[4]
        flux  = pars[5]

        if r50 < 0.0001:
            raise GMixRangeError("low r50: %g" % r50)

        # this throws a generic runtime error so there is no way to tell what
        # went wrong

        try:
            model = self._model_class(
                half_light_radius=r50,
                flux=flux,
            )
        except RuntimeError as err:
            raise GMixRangeError(str(err))

        return model

    def _set_model_class(self):
        import galsim
        if self.model_name=='exp': 
            self._model_class=galsim.Exponential
        elif self.model_name=='dev': 
            self._model_class=galsim.DeVaucouleurs
        elif self.model_name=='gauss':
            self._model_class=galsim.Gaussian
        else:
            raise NotImplementedError("can't fit '%s'" % self.model_name)

    def get_band_pars(self, pars_in, band):
        """
        Get pars for the specified band

        input pars are [c1, c2, e1, e2, r50, flux1, flux2, ....]
        """

        pars=self._band_pars

        pars[0:5] = pars_in[0:5]
        pars[5] = pars_in[5+band]
        return pars

    def _set_fitting_pars(self, **keys):
        """
        set the fit pars, in this case for the LM algorithm
        """
        lm_pars=keys.get('lm_pars',None)
        if lm_pars is None:
            lm_pars=_default_lm_pars
        self.lm_pars=lm_pars

    def _set_totpix(self):
        """
        Make sure the data are consistent.
        """

        totpix=0
        for kobs_list in self.mb_kobs:
            for kobs in kobs_list:
                totpix += kobs.kimage.array.size

        self.totpix=totpix


    def _convert2kobs(self, obs):
        kobs = observation.make_kobs(obs, **self.keys)

        return kobs

    def _set_kobs(self, obs_in, **keys):
        """
        Input should be an Observation, ObsList, or MultiBandObsList
        """

        if isinstance(obs_in, (Observation, ObsList, MultiBandObsList)):
            kobs=self._convert2kobs(obs_in)
        else:
            kobs=observation.get_kmb_obs(obs_in)

        self.mb_kobs = kobs
        self.nband=len(kobs)

    def _set_prior(self, **keys):
        self.prior = keys.get('prior',None)
        if self.prior is None:
            self.n_prior_pars=0
        else:
            #                 c1  c2  e1e2  r50  fluxes
            self.n_prior_pars=1 + 1 + 1   + 1  + self.nband

    def _set_fdiff_size(self):
        # we have 2*totpix, since we use both real and imaginary 
        # parts
        self.fdiff_size = self.n_prior_pars + 2*self.totpix


    def _create_models_in_kobs(self, kobs):
        ex=kobs.kimage

        meta=kobs.meta
        meta['kmodel'] = ex.copy()
        meta['scratch'] = ex.copy()

    def _init_model_images(self):
        """
        add model image entries to the metadata for
        each observation

        these will get filled in
        """

        for kobs_list in self.mb_kobs:
            for kobs in kobs_list:
                meta=kobs.meta

                weight = kobs.weight
                ierr = weight.copy()
                ierr.setZero()

                w=numpy.where(weight.array > 0)
                if w[0].size > 0:
                    ierr.array[w] = numpy.sqrt(weight.array[w])

                meta['ierr'] = ierr
                self._create_models_in_kobs(kobs)

    def _check_guess(self, guess):
        """
        check the guess by making a model and checking for an
        exception
        """

        guess=numpy.array(guess,dtype='f8',copy=False)
        if guess.size != self.npars:
            raise ValueError("expected %d entries in the "
                             "guess, but got %d" % (self.npars,guess.size))

        for band in xrange(self.nband):
            band_pars = self.get_band_pars(guess, band)
            gal = self.make_model(band_pars)

        return guess

    def _get_guess(self, guess):
        """
        make sure the guess has the right size and meets the model
        restrictions
        """

        guess=self._check_guess(guess)
        return guess


    def _set_npars(self):
        """
        nband should be set in set_lists, called before this
        """
        self.npars=5 + self.nband

    def _set_band_pars(self):
        """
        this is the array we fill with pars for a specific band
        """
        self._set_npars()

        npars_band = self.npars - self.nband + 1
        self._band_pars=numpy.zeros(npars_band)

    def get_fit_stats(self, pars):
        """
        Get some fit statistics for the input pars.
        """

        res={}
        res['s2n_r'] = self.calc_s2n_r(pars)
        return res

    def calc_s2n_r(self, pars):
        """
        we already have the round r50, so just create the
        models and don't shear them
        """

        s2n_sum=0.0
        for band,kobs_list in enumerate(self.mb_kobs):
            # pars for this band, in linear space
            band_pars=self.get_band_pars(pars, band)

            for i,kobs in enumerate(kobs_list):
                meta=kobs.meta
                weight=kobs.weight

                round_pars=band_pars.copy()
                round_pars[2:2+2] = 0.0
                gal = self.make_model(round_pars)

                kmodel=meta['kmodel']

                gal.drawKImage(image=kmodel)

                kmodel *= kobs.psf.kimage
                kmodel.real.array[:,:] *= kmodel.real.array[:,:]
                kmodel.imag.array[:,:] *= kmodel.imag.array[:,:]

                kmodel.real.array[:,:] *= weight.array[:,:]
                kmodel.imag.array[:,:] *= weight.array[:,:]

                s2n_sum += kmodel.real.array.sum()
                s2n_sum += kmodel.imag.array.sum()

        if s2n_sum > 0.0:
            s2n = numpy.sqrt(s2n_sum)
        else:
            s2n = 0.0

        return s2n

class SpergelFitter(GalsimSimple):
    """
    Fit the spergel profile to the input observations
    """
    def __init__(self, obs, **keys):
        super(SpergelFitter,self).__init__(obs, 'spergel', **keys)

    def _set_model_class(self):
        import galsim
        
        self._model_class=galsim.Spergel

    def make_round_model(self, pars):
        """
        make the galsim Spergel model
        """
        import galsim

        r50   = pars[4]
        nu    = pars[5]
        flux  = pars[6]

        # generic RuntimeError thrown
        try:
            gal = galsim.Spergel(
                nu,
                half_light_radius=r50,
                flux=flux,
            )
        except RuntimeError as err:
            raise GMixRangeError(str(err))

        return gal

    def get_band_pars(self, pars_in, band):
        """
        Get linear pars for the specified band

        input pars are [c1, c2, e1, e2, r50, nu, flux1, flux2, ....]
        """

        pars=self._band_pars

        pars[0:6] = pars_in[0:6]
        pars[6] = pars_in[6+band]
        return pars

    def _set_prior(self, **keys):
        self.prior = keys.get('prior',None)
        if self.prior is None:
            self.n_prior_pars=0
        else:
            #                 c1  c2  e1e2  r50  nu   fluxes
            self.n_prior_pars=1 + 1 + 1   + 1  + 1  + self.nband


    def _set_npars(self):
        """
        nband should be set in set_lists, called before this
        """
        self.npars=6 + self.nband

class GalsimTemplateFluxFitter(TemplateFluxFitter):
    def __init__(self, obs, model, psf_models, **keys):
        """
        TODO:
            
            - center
            - flux is always about 1

        parameters
        -----------
        obs: Observation or ObsList
            See ngmix.observation.Observation.
        model: galsim model
            A Galsim model, e.g. Exponential
        psf_models: galsim model or list thereof
            the psf models should have the same shape as the obs
        """

        self.model=model
        self.psf_models=psf_models


        self.keys=keys
        self.normalize_psf = keys.get('normalize_psf',True)

        self.simulate_err=keys.get('simulate_err',False)
        if self.simulate_err:
            rng=keys.get("rng",None)
            if rng is None:
                rng = numpy.random.RandomState()
            self.rng=rng

        self.set_obs(obs)

        self.model_name='template'
        self.npars=1

        self._set_totpix()


    def go(self):
        """
        calculate the flux using zero-lag cross-correlation
        """

        flags=0

        xcorr_sum=0.0
        msq_sum=0.0

        chi2=0.0

        nobs=len(self.obs)

        flux=PDEF
        flux_err=CDEF

        for ipass in [1,2]:
            for iobs in xrange(nobs):
                obs=self.obs[iobs]

                model = self.image_list[iobs]

                im=obs.image
                wt=obs.weight

                if ipass==1:
                    xcorr_sum += (model*im*wt).sum()
                    msq_sum += (model*model*wt).sum()
                else:
                    model *= (flux/model.sum())

                    if self.simulate_err:
                        err = numpy.zeros(model.shape)
                        w=numpy.where(wt > 0)
                        err[w] = numpy.sqrt(1.0/wt[w])
                        noisy_model = model.copy()
                        noisy_model += self.rng.normal(size=model.shape)*err
                        chi2 +=( (model-noisy_model)**2 *wt ).sum()
                    else:
                        chi2 +=( (model-im)**2 *wt ).sum()

            if ipass==1:
                if msq_sum==0:
                    break
                flux = xcorr_sum/msq_sum

        # chi^2 per dof and error checking
        dof=self.get_dof()
        chi2per=9999.0
        if dof > 0:
            chi2per=chi2/dof
        else:
            flags |= ZERO_DOF

        # final flux calculation with error checking
        if msq_sum==0 or self.totpix==1:
            flags |= DIV_ZERO
        else:

            arg=chi2/msq_sum/(self.totpix-1)
            if arg >= 0.0:
                flux_err = numpy.sqrt(arg)
            else:
                flags |= BAD_VAR

        self._result={'model':self.model_name,
                      'flags':flags,
                      'chi2per':chi2per,
                      'dof':dof,
                      'flux':flux,
                      'flux_err':flux_err}

    def set_obs(self, obs_in):
        """
        Input should be an Observation, ObsList
        """
        import galsim


        if isinstance(obs_in,Observation):
            obs_list=ObsList()
            obs_list.append(obs_in)
            
            self.psf_models = [self.psf_models]

        elif isinstance(obs_in,ObsList):
            obs_list=obs_in

            if len(self.psf_models) != obs_list:
                raise ValueError("psf models must be same size as obs list")
        else:
            raise ValueError("obs should be Observation or ObsList")

        image_list=[]

        model = self.model.withFlux(1.0)

        for i,obs in enumerate(obs_list):

            psf_model=self.psf_models[i]

            if self.normalize_psf:
                psf_model = psf_model.withFlux(1.0)

            obj = galsim.Convolve(model, psf_model)

            wcs = obs.jacobian.get_galsim_wcs()

            nrow,ncol=obs.image.shape

            try:
                gim = obj.drawImage(
                    nx=ncol,
                    ny=nrow,
                    wcs=wcs,
                    method='no_pixel', # pixel is assumed to be in psf
                )
            except RuntimeError as err:
                # argh another generic exception
                raise GMixRangeError(str(err))

            image_list.append( gim.array )

        self.obs = obs_list
        self.image_list=image_list


